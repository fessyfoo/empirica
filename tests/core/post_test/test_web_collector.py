"""Tests for the web evidence collector."""

import json

from empirica.core.post_test.collector import EvidenceProfile
from empirica.core.post_test.web_collector import (
    WEB_EXTENSIONS,
    WebEvidenceCollector,
    _HTMLStructureValidator,
)

# --- HTML Validator Tests ---


class TestHTMLValidator:
    def test_valid_html(self):
        v = _HTMLStructureValidator()
        v.feed("""<!DOCTYPE html>
        <html><head><meta charset="utf-8"><title>Test</title></head>
        <body><h1>Hello</h1></body></html>""")
        v.finalize()
        assert len(v.errors) == 0
        assert len(v.warnings) == 0

    def test_missing_structure(self):
        v = _HTMLStructureValidator()
        v.feed("<p>Just a paragraph</p>")
        v.finalize()
        assert len(v.errors) == 0
        assert len(v.warnings) > 0
        warning_text = " ".join(v.warnings)
        assert "html" in warning_text.lower()
        assert "head" in warning_text.lower()

    def test_unclosed_tags(self):
        v = _HTMLStructureValidator()
        v.feed("<html><body><div><p>Unclosed</div></body></html>")
        v.finalize()
        assert any("Unclosed" in e or "Mismatched" in e for e in v.errors)

    def test_empty_href(self):
        v = _HTMLStructureValidator()
        v.feed('<html><body><a href="">Empty link</a></body></html>')
        v.finalize()
        assert any("empty href" in e for e in v.errors)

    def test_empty_src(self):
        v = _HTMLStructureValidator()
        v.feed('<html><body><img src=""></body></html>')
        v.finalize()
        assert any("empty src" in e for e in v.errors)

    def test_void_elements_no_close(self):
        v = _HTMLStructureValidator()
        v.feed(
            '<html><head><meta charset="utf-8"><link rel="stylesheet" href="style.css"></head><body><br><hr><img src="logo.png"></body></html>'
        )
        v.finalize()
        # Void elements should NOT cause unclosed tag errors
        assert not any("Unclosed" in e for e in v.errors)

    def test_charset_detection(self):
        v = _HTMLStructureValidator()
        v.feed('<html><head><meta charset="utf-8"><title>T</title></head><body></body></html>')
        v.finalize()
        assert not any("charset" in w for w in v.warnings)

    def test_no_charset_warning(self):
        v = _HTMLStructureValidator()
        v.feed("<html><head><title>T</title></head><body></body></html>")
        v.finalize()
        assert any("charset" in w for w in v.warnings)


# --- Build Detection Tests ---


class TestBuildDetection:
    def test_detect_astro(self, tmp_path):
        (tmp_path / "astro.config.mjs").touch()
        collector = WebEvidenceCollector(session_id="test")
        collector._project_path = tmp_path
        result = collector._detect_build_tool()
        assert result is not None
        assert result["tool"] == "astro"
        assert "astro build" in result["command"]

    def test_detect_next(self, tmp_path):
        (tmp_path / "next.config.js").touch()
        collector = WebEvidenceCollector(session_id="test")
        collector._project_path = tmp_path
        result = collector._detect_build_tool()
        assert result is not None
        assert result["tool"] == "next"

    def test_detect_npm_build(self, tmp_path):
        pkg = {"scripts": {"build": "vite build"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        collector = WebEvidenceCollector(session_id="test")
        collector._project_path = tmp_path
        result = collector._detect_build_tool()
        assert result is not None
        assert result["tool"] == "npm"

    def test_detect_makefile(self, tmp_path):
        (tmp_path / "Makefile").write_text("build:\n\thugo --minify\n")
        collector = WebEvidenceCollector(session_id="test")
        collector._project_path = tmp_path
        result = collector._detect_build_tool()
        assert result is not None
        assert result["tool"] == "make"

    def test_no_build_tool(self, tmp_path):
        collector = WebEvidenceCollector(session_id="test")
        collector._project_path = tmp_path
        assert collector._detect_build_tool() is None

    def test_custom_build_command(self, tmp_path):
        (tmp_path / ".empirica").mkdir()
        import yaml

        config = {"web_evidence": {"build_command": "hugo build", "output_dir": "public"}}
        (tmp_path / ".empirica" / "project.yaml").write_text(yaml.dump(config))
        collector = WebEvidenceCollector(session_id="test")
        collector._project_path = tmp_path
        result = collector._detect_build_tool()
        assert result["tool"] == "custom"
        assert result["command"] == "hugo build"
        assert result["output_dir"] == "public"

    def test_npm_without_build_script(self, tmp_path):
        pkg = {"scripts": {"start": "node index.js"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        collector = WebEvidenceCollector(session_id="test")
        collector._project_path = tmp_path
        assert collector._detect_build_tool() is None


# --- Link Integrity Tests ---


class TestLinkIntegrity:
    def _setup_site(self, tmp_path):
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text(
            '<a href="/about/">About</a>'
            '<a href="/missing">Missing</a>'
            '<a href="https://example.com">External</a>'
            '<link href="/style.css" rel="stylesheet">'
        )
        about = dist / "about"
        about.mkdir()
        (about / "index.html").write_text("<h1>About</h1>")
        (dist / "style.css").write_text("body {}")
        return dist

    def test_link_checking(self, tmp_path):
        self._setup_site(tmp_path)
        collector = WebEvidenceCollector(session_id="test")
        collector._project_path = tmp_path
        collector._web_config = {"output_dir": "dist"}

        items = collector._collect_link_integrity()
        assert len(items) == 1
        item = items[0]
        assert item.metric_name == "link_integrity"
        # /about/ and /style.css valid, /missing broken
        assert item.raw_value["valid"] == 2
        assert len(item.raw_value["broken"]) == 1
        assert item.raw_value["external_skipped"] == 1

    def test_no_output_dir(self, tmp_path):
        collector = WebEvidenceCollector(session_id="test")
        collector._project_path = tmp_path
        assert collector._collect_link_integrity() == []


# --- Terminology Tests ---


class TestTerminologyConsistency:
    def _setup_glossary(self, tmp_path):
        import yaml

        empirica_dir = tmp_path / ".empirica"
        empirica_dir.mkdir()
        glossary = {
            "terms": [
                {"canonical": "Sentinel", "incorrect": ["Cognitive Sentinel"]},
                {"canonical": "CASCADE", "incorrect": ["5-phase CASCADE"]},
            ]
        }
        (empirica_dir / "glossary.yaml").write_text(yaml.dump(glossary))

    def test_clean_terminology(self, tmp_path):
        self._setup_glossary(tmp_path)
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html><body>The Sentinel gates actions. CASCADE has 3 phases.</body></html>")
        collector = WebEvidenceCollector(session_id="test")
        collector._project_path = tmp_path
        collector._web_config = {"output_dir": "dist"}

        items = collector._collect_terminology_consistency()
        assert len(items) == 1
        assert items[0].value == 1.0
        assert items[0].raw_value["violation_count"] == 0

    def test_terminology_violations(self, tmp_path):
        self._setup_glossary(tmp_path)
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text(
            "<html><body>The Cognitive Sentinel gates the 5-phase CASCADE workflow.</body></html>"
        )
        collector = WebEvidenceCollector(session_id="test")
        collector._project_path = tmp_path
        collector._web_config = {"output_dir": "dist"}

        items = collector._collect_terminology_consistency()
        assert len(items) == 1
        assert items[0].value < 1.0
        assert items[0].raw_value["violation_count"] == 2

    def test_no_glossary_skips(self, tmp_path):
        (tmp_path / ".empirica").mkdir()
        collector = WebEvidenceCollector(session_id="test")
        collector._project_path = tmp_path
        assert collector._collect_terminology_consistency() == []


# --- Asset Verification Tests ---


class TestAssetVerification:
    def test_assets_found(self, tmp_path):
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "logo.png").write_bytes(b"\x89PNG")
        (dist / "index.html").write_text('<img src="/logo.png"><img src="https://cdn.example.com/remote.png">')
        collector = WebEvidenceCollector(session_id="test")
        collector._project_path = tmp_path
        collector._web_config = {"output_dir": "dist"}

        items = collector._collect_asset_verification()
        assert len(items) == 1
        assert items[0].value == 1.0
        assert items[0].raw_value["found"] == 1
        assert items[0].raw_value["total"] == 1  # external skipped

    def test_missing_assets(self, tmp_path):
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text('<img src="/missing.png">')
        collector = WebEvidenceCollector(session_id="test")
        collector._project_path = tmp_path
        collector._web_config = {"output_dir": "dist"}

        items = collector._collect_asset_verification()
        assert len(items) == 1
        assert items[0].value == 0.0
        assert len(items[0].raw_value["missing"]) == 1

    def test_public_dir_fallback(self, tmp_path):
        dist = tmp_path / "dist"
        dist.mkdir()
        public = tmp_path / "public"
        public.mkdir()
        (public / "favicon.ico").write_bytes(b"\x00")
        (dist / "index.html").write_text('<link href="/favicon.ico">')
        collector = WebEvidenceCollector(session_id="test")
        collector._project_path = tmp_path
        collector._web_config = {"output_dir": "dist", "public_dir": "public"}

        items = collector._collect_asset_verification()
        assert len(items) == 1
        assert items[0].value == 1.0


# --- Profile Auto-Detection Tests ---


class TestProfileAutoDetection:
    def test_web_extensions(self):
        assert ".astro" in WEB_EXTENSIONS
        assert ".html" in WEB_EXTENSIONS
        assert ".jsx" in WEB_EXTENSIONS
        assert ".tsx" in WEB_EXTENSIONS
        assert ".vue" in WEB_EXTENSIONS
        assert ".svelte" in WEB_EXTENSIONS
        assert ".mdx" in WEB_EXTENSIONS
        assert ".py" not in WEB_EXTENSIONS

    def test_web_profile_valid(self):
        assert EvidenceProfile.WEB in EvidenceProfile.VALID
        assert EvidenceProfile.WEB == "web"


# --- HTML Validation on Real-ish Content ---


class TestHTMLValidation:
    def test_validate_multiple_files(self, tmp_path):
        dist = tmp_path / "dist"
        dist.mkdir()

        # Good file
        (dist / "good.html").write_text(
            '<html><head><meta charset="utf-8"><title>Good</title></head><body><h1>Good Page</h1></body></html>'
        )
        # Bad file (unclosed div)
        (dist / "bad.html").write_text(
            '<html><head><meta charset="utf-8"><title>Bad</title></head><body><div><p>Oops</body></html>'
        )

        collector = WebEvidenceCollector(session_id="test")
        collector._project_path = tmp_path
        collector._web_config = {"output_dir": "dist"}

        items = collector._collect_html_validation()
        assert len(items) == 1
        item = items[0]
        assert item.metric_name == "html_validity"
        assert item.raw_value["files_checked"] == 2
        assert item.raw_value["total_errors"] > 0
        assert item.value < 1.0  # Some errors
        assert item.value > 0.0  # Not all broken
