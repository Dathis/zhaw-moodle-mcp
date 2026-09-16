import pytest

from zhaw_moodle_mcp.moodle.parser import (
    clean_text,
    filename_from_url,
    normalize_etag,
    parse_activity_icons,
    parse_folder_files,
    parse_page_session,
    sanitize_component,
    section_dir_name,
    type_from_filename,
    type_from_icon,
)

from .conftest import BASE, GUEST_PAGE, LOGGED_IN_PAGE, page


def test_logged_in_page():
    s = parse_page_session(LOGGED_IN_PAGE)
    assert s.logged_in and s.user_id == 4242 and s.sesskey == "SK123"


def test_guest_page_is_not_logged_in():
    assert not parse_page_session(GUEST_PAGE).logged_in


def test_login_link_means_not_logged_in():
    assert not parse_page_session(page(4242, login_link=True)).logged_in


def test_page_without_moodle_config():
    assert not parse_page_session("<html><body>SSO</body></html>").logged_in


def test_activity_icons():
    html = f"""
    <ul>
      <li class="activity activity-wrapper resource modtype_resource" data-id="11">
        <img src="{BASE}/theme/image.php/boost_union/core/1/f/pdf?filtericon=1"></li>
      <li class="activity modtype_resource" data-id="12">
        <img src="{BASE}/theme/image.php/boost/core/1/f/powerpoint-24"></li>
      <li class="activity modtype_page" data-id="13">
        <img src="{BASE}/theme/image.php/boost/page/1/monologo?filtericon=1"></li>
    </ul>"""
    assert parse_activity_icons(html) == {11: "pdf", 12: "powerpoint"}


def test_folder_files_dedup_and_subdirs():
    html = f"""
    <a href="{BASE}/pluginfile.php/99/mod_folder/content/0/Slides%20W1.pdf?forcedownload=1">x</a>
    <a href="{BASE}/pluginfile.php/99/mod_folder/content/0/Slides%20W1.pdf?forcedownload=1">y</a>
    <a href="/pluginfile.php/99/mod_folder/content/3/sub/Ex%C3%BCbung.docx?forcedownload=1">z</a>
    <a href="{BASE}/pluginfile.php/99/mod_folder/intro/pic.png">intro</a>"""
    files = parse_folder_files(html, BASE)
    assert [f.path for f in files] == ["Slides W1.pdf", "sub/Exübung.docx"]
    assert files[1].url == f"{BASE}/pluginfile.php/99/mod_folder/content/3/sub/Ex%C3%BCbung.docx"


def test_filename_from_url():
    assert filename_from_url(f"{BASE}/pluginfile.php/1/mod_resource/content/2/OR%20Einf.pdf") == "OR Einf.pdf"


@pytest.mark.parametrize(("raw", "expected"), [
    ("SW 1: St. Galler &amp; Co", "SW 1_ St. Galler & Co"),
    ('a<b>c:"d"/e\\f|g?h*', "a_b_c__d__e_f_g_h_"),
    ("  trailing dots... ", "trailing dots"),
    ("CON", "_CON"),
    ("con.txt", "_con.txt"),
    ("..", "_"),
    ("", "_"),
])
def test_sanitize_component(raw, expected):
    assert sanitize_component(raw) == expected


def test_sanitize_keeps_extension_when_truncating():
    name = sanitize_component("x" * 200 + ".pdf", max_length=50)
    assert len(name) == 50 and name.endswith(".pdf")


def test_section_dir_name():
    assert section_dir_name(3, "SW 2 &amp; 3") == "03 SW 2 & 3"
    assert section_dir_name(None, "Sub") == "Sub"


def test_types():
    assert type_from_icon("document") == "word"
    assert type_from_icon(None) == "file"
    assert type_from_filename("A.PPTX") == "powerpoint"
    assert type_from_filename("noext") == "file"


def test_clean_text():
    assert clean_text("  a &amp;\n b ") == "a & b"


def test_normalize_etag():
    assert normalize_etag('"abc-gzip"') == normalize_etag('"abc"') == "abc"
    assert normalize_etag('W/"abc"') == "abc"
    assert normalize_etag(None) is None
