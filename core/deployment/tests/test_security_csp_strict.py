"""Тесты контроля csp.strict и билдера CSP (security audit С11)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import _bootstrap  # noqa: F401

from render_common import render_docker_nginx_config
from security.catalog import load_security_catalog
from security.checkers import _REGISTRY
from security.checkers.csp_strict import run as csp_strict_run
from security.csp_policy import (
    build_csp_policy,
    build_security_headers_nginx,
    normalize_csp_mode,
    resolve_csp_mode,
    substitute_security_headers_includes,
)
from security.profile_defaults import merge_security_profile_defaults


class CspPolicyBuilderTests(unittest.TestCase):
    def test_normalize_unknown_falls_back(self) -> None:
        self.assertEqual(normalize_csp_mode(None), 'as_is')
        self.assertEqual(normalize_csp_mode(''), 'as_is')
        self.assertEqual(normalize_csp_mode('weird'), 'as_is')
        self.assertEqual(normalize_csp_mode('NO_UNSAFE'), 'no_unsafe')

    def test_as_is_has_unsafe(self) -> None:
        policy = build_csp_policy('as_is')
        self.assertIn("'unsafe-eval'", policy)
        self.assertIn("'unsafe-inline'", policy)
        self.assertIn('api-maps.yandex.ru', policy)

    def test_no_unsafe_drops_unsafe_keeps_maps(self) -> None:
        policy = build_csp_policy('no_unsafe')
        self.assertNotIn('unsafe-eval', policy)
        self.assertNotIn('unsafe-inline', policy)
        self.assertIn('api-maps.yandex.ru', policy)
        self.assertIn("img-src 'self' data: blob: https:", policy)

    def test_maximum_stub_tightens_externals(self) -> None:
        policy = build_csp_policy('no_unsafe_plus_externals')
        self.assertNotIn('unsafe-eval', policy)
        self.assertNotIn('unsafe-inline', policy)
        self.assertNotIn('blob: https:;', policy)
        self.assertNotIn('api-maps.yandex.ru', policy)
        self.assertIn("script-src 'self'", policy)
        self.assertIn('*.maps.yandex.net', policy)

    def test_extra_script_hash_appends_to_script_src(self) -> None:
        digest = 'sha256-GcFCXGcRBfkVT0d83GA/YpsKpjnsB2jkZ47h+yRpvcI='
        policy = build_csp_policy(
            'no_unsafe_plus_externals',
            extra_script_hashes=[digest],
        )
        self.assertIn(f"script-src 'self' '{digest}'", policy)
        self.assertNotIn('unsafe-inline', policy)

    def test_extra_script_hash_keeps_as_is_maps(self) -> None:
        digest = 'sha256-GcFCXGcRBfkVT0d83GA/YpsKpjnsB2jkZ47h+yRpvcI='
        policy = build_csp_policy('as_is', extra_script_hashes=[digest])
        self.assertIn(f"script-src 'self' '{digest}' 'unsafe-eval'", policy)
        self.assertIn('api-maps.yandex.ru', policy)

    def test_nginx_snippet_embeds_policy(self) -> None:
        block = build_security_headers_nginx('no_unsafe')
        self.assertIn('X-Frame-Options', block)
        self.assertIn('Content-Security-Policy', block)
        self.assertNotIn('unsafe-eval', block)

    def test_resolve_merges_profile(self) -> None:
        self.assertEqual(resolve_csp_mode({'ERGO_SECURITY': 'hardened'}), 'no_unsafe')
        self.assertEqual(
            resolve_csp_mode({'ERGO_SECURITY': 'hardened', 'API_CSP_MODE': 'as_is'}),
            'as_is',
        )

    def test_substitute_preserves_indent(self) -> None:
        template = (
            'server {\n'
            '    include ${ERGO_NGINX_SNIPPETS}/security_headers.conf;\n'
            '}\n'
        )
        out = substitute_security_headers_includes(
            template,
            build_security_headers_nginx('as_is'),
        )
        self.assertNotIn('security_headers.conf', out)
        self.assertIn('    add_header X-Frame-Options', out)
        self.assertIn('Content-Security-Policy', out)

    def test_substitute_resolved_snippet_path(self) -> None:
        template = (
            '    location = /index.html {\n'
            '        include /opt/ergo_ms_core/core/deployment/nginx/snippets/security_headers.conf;\n'
            '    }\n'
        )
        out = substitute_security_headers_includes(
            template,
            build_security_headers_nginx(
                'as_is',
                extra_script_hashes=['sha256-GcFCXGcRBfkVT0d83GA/YpsKpjnsB2jkZ47h+yRpvcI='],
            ),
        )
        self.assertNotIn('security_headers.conf', out)
        self.assertIn("'sha256-GcFCXGcRBfkVT0d83GA/YpsKpjnsB2jkZ47h+yRpvcI='", out)


class CspStrictCheckerTests(unittest.TestCase):
    def setUp(self) -> None:
        catalog = load_security_catalog()
        self.control = catalog.control_by_id('csp.strict')
        self.catalog = catalog
        self.assertIsNotNone(self.control)
        self.assertEqual(self.control.check, 'csp_strict')
        self.assertEqual(self.control.status, 'partial')
        self.assertEqual(self.control.env_key, 'API_CSP_MODE')
        self.assertIn('csp_strict', _REGISTRY)

    def _run(self, *, level: str, values: dict[str, str]):
        return csp_strict_run(
            self.control,
            self.catalog,
            {'values': values, 'level': level, 'root': '.'},
        )

    def test_standard_ok_default(self) -> None:
        finding = self._run(level='standard', values={})
        self.assertEqual(finding.severity, 'ok')

    def test_hardened_unset_ok(self) -> None:
        finding = self._run(level='hardened', values={})
        self.assertEqual(finding.severity, 'ok')

    def test_hardened_explicit_no_unsafe_ok(self) -> None:
        finding = self._run(
            level='hardened',
            values={'API_CSP_MODE': 'no_unsafe'},
        )
        self.assertEqual(finding.severity, 'ok')

    def test_hardened_weaker_as_is_warning(self) -> None:
        finding = self._run(
            level='hardened',
            values={'API_CSP_MODE': 'as_is'},
        )
        self.assertEqual(finding.severity, 'warning')
        self.assertIn('as_is', finding.message)

    def test_maximum_partial_warning(self) -> None:
        finding = self._run(
            level='maximum',
            values={'API_CSP_MODE': 'no_unsafe_plus_externals'},
        )
        self.assertEqual(finding.severity, 'warning')
        self.assertIn('phase 1', finding.message)

    def test_maximum_weaker_no_unsafe_warning(self) -> None:
        finding = self._run(
            level='maximum',
            values={'API_CSP_MODE': 'no_unsafe'},
        )
        self.assertEqual(finding.severity, 'warning')
        self.assertIn('no_unsafe', finding.message)


class CspMergeTests(unittest.TestCase):
    def test_standard_injects_as_is(self) -> None:
        merged = merge_security_profile_defaults({'ERGO_SECURITY': 'standard'})
        self.assertEqual(merged['API_CSP_MODE'], 'as_is')

    def test_hardened_injects_no_unsafe(self) -> None:
        merged = merge_security_profile_defaults({'ERGO_SECURITY': 'hardened'})
        self.assertEqual(merged['API_CSP_MODE'], 'no_unsafe')

    def test_maximum_injects_externals_mode(self) -> None:
        merged = merge_security_profile_defaults({'ERGO_SECURITY': 'maximum'})
        self.assertEqual(merged['API_CSP_MODE'], 'no_unsafe_plus_externals')

    def test_explicit_kept(self) -> None:
        merged = merge_security_profile_defaults({
            'ERGO_SECURITY': 'hardened',
            'API_CSP_MODE': 'as_is',
        })
        self.assertEqual(merged['API_CSP_MODE'], 'as_is')


class CspNginxRenderTests(unittest.TestCase):
    def test_docker_render_uses_no_unsafe_when_set(self) -> None:
        deployment_dir = Path(__file__).resolve().parents[1]
        docker_template = deployment_dir / 'docker' / 'nginx' / 'ergo_ms.docker.conf.template'
        raw_env = {
            'DOCKER_SERVICE_API': 'api',
            'DOCKER_SERVICE_MEDIA': 'media-api',
            'API_PORT': '8000',
            'MEDIA_API_BIND_PORT': '8003',
            'NGINX_LISTEN_PORT': '80',
            'NGINX_SERVER_NAME': 'localhost',
            'API_CSP_MODE': 'no_unsafe',
        }
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'docker.conf'
            render_docker_nginx_config(raw_env, template_path=docker_template, output_path=out)
            rendered = out.read_text(encoding='utf-8')
        self.assertIn('Content-Security-Policy', rendered)
        self.assertNotIn('unsafe-eval', rendered)
        self.assertIn('api-maps.yandex.ru', rendered)


class MiddlewareModeSelectionTests(unittest.TestCase):
    def test_middleware_sets_csp_from_builder(self) -> None:
        import importlib.util
        from unittest import mock

        path = (
            Path(__file__).resolve().parents[1].parent
            / 'api'
            / 'src'
            / 'core'
            / 'utils'
            / 'middleware'
            / 'security_headers_middleware.py'
        )
        spec = importlib.util.spec_from_file_location('security_headers_middleware_s11', path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        response = MagicMock()
        response.headers = {}

        def get_response(_request):
            return response

        policy = build_csp_policy('no_unsafe')
        with mock.patch.object(mod, '_csp_header', return_value=policy):
            middleware = mod.SecurityHeadersMiddleware(get_response)
            out = middleware(MagicMock())
        self.assertEqual(out.headers['Content-Security-Policy'], policy)
        self.assertNotIn('unsafe-eval', out.headers['Content-Security-Policy'])


if __name__ == '__main__':
    unittest.main()
