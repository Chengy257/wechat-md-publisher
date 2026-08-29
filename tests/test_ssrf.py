"""SSRF protection for remote image downloads (offline: no real DNS or HTTP)."""

import socket
from pathlib import Path

import pytest
from conftest import make_png

from wechat_publish.config import resolve_config
from wechat_publish.images import (
    UploadedBodyImage,
    _resolve_image_to_file,
    _validate_remote_url,
    process_images,
)


class _FakeResponse:
    def __init__(self, status_code=200, headers=None, content=b""):
        self.status_code = status_code
        self.headers = headers or {}
        self._content = content

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=None):
        yield self._content


class _RequestRecorder:
    """Install a fake wechat_publish.http.requests.request and record URLs."""

    def __init__(self, monkeypatch, handler):
        self.calls: list[str] = []
        self._handler = handler

        def fake_request(method, url, **kwargs):
            self.calls.append(url)
            assert kwargs.get("allow_redirects") is False, (
                "downloads must not follow redirects inside requests"
            )
            return handler(url)

        monkeypatch.setattr("wechat_publish.http.requests.request", fake_request)
        monkeypatch.setattr("wechat_publish.http.time.sleep", lambda s: None)


@pytest.fixture
def _no_real_dns(monkeypatch):
    """Guard against accidental real DNS resolution in any test here."""
    def _fail(*args, **kwargs):
        raise AssertionError("real socket.getaddrinfo called in test")

    monkeypatch.setattr(socket, "getaddrinfo", _fail)


# ── URL validation ──────────────────────────────────────────────

class TestValidateRemoteUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1/x.png",
            "http://10.0.0.1/x.png",
            "http://192.168.1.5/x.png",
            "http://172.16.0.9/x.png",
            "http://169.254.169.254/latest/meta-data",
            "http://0.0.0.0/x.png",
            "http://[::1]/x.png",
            "http://[fe80::1]/x.png",
        ],
    )
    def test_blocked_ip_literals_rejected_before_any_request(
        self, url, monkeypatch
    ):
        _RequestRecorder(
            monkeypatch,
            lambda u: (_ for _ in ()).throw(AssertionError("request must not be issued")),
        )
        with pytest.raises(ValueError, match="private/blocked"):
            _validate_remote_url(url)

    @pytest.mark.parametrize("url", ["http://localhost/x.png", "http://LOCALHOST/x.png"])
    def test_localhost_blocked_case_insensitive(self, url, monkeypatch):
        _RequestRecorder(
            monkeypatch,
            lambda u: (_ for _ in ()).throw(AssertionError("request must not be issued")),
        )
        with pytest.raises(ValueError, match="localhost"):
            _validate_remote_url(url)

    def test_domain_resolving_to_private_ip_blocked(self, monkeypatch):
        def fake_getaddrinfo(host, port, *args, **kwargs):
            return [(2, 1, 6, "", ("10.0.0.7", 0))]

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        with pytest.raises(ValueError, match="private/blocked"):
            _validate_remote_url("http://internal.example.com/x.png")

    def test_all_resolved_ips_must_be_public(self, monkeypatch):
        def fake_getaddrinfo(host, port, *args, **kwargs):
            return [
                (2, 1, 6, "", ("93.184.216.34", 0)),
                (2, 1, 6, "", ("192.168.0.1", 0)),  # one bad record blocks all
            ]

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        with pytest.raises(ValueError, match="private/blocked"):
            _validate_remote_url("http://mixed.example.com/x.png")

    def test_public_domain_passes(self, monkeypatch):
        def fake_getaddrinfo(host, port, *args, **kwargs):
            return [(2, 1, 6, "", ("93.184.216.34", 0))]

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        _validate_remote_url("http://cdn.example.com/x.png")

    def test_dns_resolution_failure_blocked(self, monkeypatch):
        def fake_getaddrinfo(host, port, *args, **kwargs):
            raise socket.gaierror("name resolution failure")

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        with pytest.raises(ValueError, match="could not be resolved"):
            _validate_remote_url("http://nonexistent.example.com/x.png")

    @pytest.mark.parametrize("url", ["ftp://example.com/x.png", "file:///etc/passwd"])
    def test_non_http_schemes_rejected(self, url, _no_real_dns):
        with pytest.raises(ValueError, match="http or https"):
            _validate_remote_url(url)

    def test_error_message_carries_allow_private_hint(self, monkeypatch):
        with pytest.raises(ValueError, match="allow_private_networks: true"):
            _validate_remote_url("http://127.0.0.1/x.png")

    def test_allow_private_skips_network_checks(self, _no_real_dns):
        _validate_remote_url("http://127.0.0.1/x.png", allow_private=True)
        _validate_remote_url("http://localhost/x.png", allow_private=True)
        _validate_remote_url("http://169.254.169.254/x.png", allow_private=True)

    def test_allow_private_keeps_scheme_check(self, _no_real_dns):
        with pytest.raises(ValueError, match="http or https"):
            _validate_remote_url("ftp://127.0.0.1/x.png", allow_private=True)


# ── redirect handling in the download path ──────────────────────

class TestRedirectLoop:
    def _resolve(self, url, allow_private=False):
        return _resolve_image_to_file(url, None, True, allow_private=allow_private)

    def test_redirect_to_private_ip_blocked(self, tmp_path, monkeypatch):
        def handler(url):
            return _FakeResponse(302, {"Location": "http://10.0.0.1/secret.png"})

        rec = _RequestRecorder(monkeypatch, handler)
        with pytest.raises(ValueError, match="private/blocked"):
            self._resolve("http://93.184.216.34/img.png")
        # The redirected-to URL must never be requested
        assert rec.calls == ["http://93.184.216.34/img.png"]

    def test_public_redirect_chain_downloads(self, tmp_path, monkeypatch):
        png = make_png(tmp_path / "real.png").read_bytes()

        def handler(url):
            if url == "http://93.184.216.34/img.png":
                return _FakeResponse(302, {"Location": "http://93.184.216.34/final.png"})
            return _FakeResponse(200, {"Content-Type": "image/png"}, png)

        _RequestRecorder(monkeypatch, handler)
        path, is_temp = self._resolve("http://93.184.216.34/img.png")

        assert is_temp is True
        assert path.read_bytes() == png
        path.unlink(missing_ok=True)

    def test_too_many_redirects_rejected(self, monkeypatch):
        count = {"hops": 0}

        def handler(url):
            count["hops"] += 1
            return _FakeResponse(
                302, {"Location": f"http://93.184.216.34/hop{count['hops']}.png"}
            )

        _RequestRecorder(monkeypatch, handler)
        with pytest.raises(ValueError, match="too many redirects"):
            self._resolve("http://93.184.216.34/img.png")

    def test_redirect_missing_location_rejected(self, monkeypatch):
        def handler(url):
            return _FakeResponse(302, {})

        _RequestRecorder(monkeypatch, handler)
        with pytest.raises(ValueError, match="Location"):
            self._resolve("http://93.184.216.34/img.png")

    def test_non_image_content_type_still_rejected(self, monkeypatch):
        def handler(url):
            return _FakeResponse(200, {"Content-Type": "text/html"}, b"<html></html>")

        _RequestRecorder(monkeypatch, handler)
        with pytest.raises(ValueError, match="non-image Content-Type"):
            self._resolve("http://93.184.216.34/img.png")


# ── process_images wiring ───────────────────────────────────────

class _Ref:
    def __init__(self, src):
        self.original_src = src
        self.resolved_path = None
        self.is_remote = True


class TestProcessImagesWiring:
    def _html(self, src):
        return f'<p><img src="{src}"></p>'

    def test_private_url_blocked_by_default(self, monkeypatch):
        _RequestRecorder(
            monkeypatch,
            lambda u: (_ for _ in ()).throw(AssertionError("request must not be issued")),
        )
        with pytest.raises(RuntimeError, match="private/blocked"):
            process_images(
                "token", self._html("http://127.0.0.1/x.png"),
                [_Ref("http://127.0.0.1/x.png")], Path("."),
            )

    def test_private_url_allowed_with_flag(self, tmp_path, monkeypatch):
        png = make_png(tmp_path / "real.png").read_bytes()

        def handler(url):
            return _FakeResponse(200, {"Content-Type": "image/png"}, png)

        _RequestRecorder(monkeypatch, handler)
        monkeypatch.setattr(
            "wechat_publish.images.upload_body_image",
            lambda token, path, cache=None: UploadedBodyImage(url="https://mmbiz/x"),
        )
        result = process_images(
            "token", self._html("http://127.0.0.1/x.png"),
            [_Ref("http://127.0.0.1/x.png")], Path("."),
            allow_private_networks=True,
        )
        assert 'src="https://mmbiz/x"' in result


# ── config resolution ───────────────────────────────────────────

def _resolve(publish_config):
    return resolve_config(
        cli_values={}, front_matter={}, publish_config=publish_config, env={},
    )


class TestRemoteImagesConfig:
    def test_default_is_false(self):
        config = _resolve({})
        assert config.remote_images_allow_private is False

    def test_true_is_parsed(self):
        config = _resolve({"remote_images": {"allow_private_networks": True}})
        assert config.remote_images_allow_private is True

    def test_explicit_false_is_parsed(self):
        config = _resolve({"remote_images": {"allow_private_networks": False}})
        assert config.remote_images_allow_private is False

    @pytest.mark.parametrize("value", ["true", 1, None, "yes"])
    def test_non_bool_rejected_cleanly(self, value):
        with pytest.raises(ValueError, match="allow_private_networks"):
            _resolve({"remote_images": {"allow_private_networks": value}})

    def test_non_mapping_remote_images_rejected(self):
        with pytest.raises(ValueError, match="remote_images"):
            _resolve({"remote_images": True})
