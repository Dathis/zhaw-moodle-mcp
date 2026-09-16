from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import respx
from bs4 import BeautifulSoup

from zhaw_moodle_mcp.errors import ErrorCode, MoodleError
from zhaw_moodle_mcp.moodle.content import embedded_file_path, parse_course_page, parse_page_view, to_markdown
from zhaw_moodle_mcp.moodle.courses import LABEL_TEXT_LIMIT
from zhaw_moodle_mcp.service import MoodleService

from .conftest import BASE, LOGGED_IN_PAGE
from .fake_moodle import COURSE_ID, FakeFile, FakeMoodle

TZ = ZoneInfo("Europe/Zurich")


def md(html: str) -> str:
    return to_markdown(BeautifulSoup(f"<div>{html}</div>", "lxml").div, BASE).text


def test_markdown_blocks_and_inline():
    assert md("<h3>Termine</h3><p>Die <b>Prüfung</b> ist <em>schriftlich</em>.<br>Dauer: 90 min</p>") == (
        "#### Termine\n\nDie **Prüfung** ist *schriftlich*.\nDauer: 90 min")


def test_markdown_lists_nested():
    html = "<ul><li>A<ul><li>A1</li></ul></li><li>B</li></ul><ol><li>eins</li><li>zwei</li></ol>"
    assert md(html) == "- A\n  - A1\n- B\n\n1. eins\n2. zwei"


def test_markdown_table_and_misc():
    html = ("<table><tr><th>LN</th><th>Gewicht</th></tr><tr><td>MEP</td><td>100 | %</td></tr></table>"
            "<hr><img alt='Logo'><span class='sr-only'>hidden</span><script>x()</script>"
            "<pre>code  block</pre><a href='mailto:a@zhaw.ch'>a@zhaw.ch</a>")
    assert md(html) == ("| LN | Gewicht |\n| --- | --- |\n| MEP | 100 \\| % |\n\n---\n\n"
                        "[Bild: Logo]\n\n```\ncode  block\n```\n\na@zhaw.ch")


def test_markdown_links_are_classified():
    node = BeautifulSoup(f"""<div>
        <a href="/mod/resource/view.php?id=12">Skript</a>
        <a href="{BASE}/pluginfile.php/1/mod_page/content/3/plan.pdf">Plan</a>
        <a href="{BASE}/course/view.php?id=7">Kurs</a>
        <a href="https://doi.org/10.1/x">DOI</a>
        <a href="https://doi.org/10.1/x">again</a>
        <a href="#top">Top</a></div>""", "lxml").div
    content = to_markdown(node, BASE)
    assert [(link.kind, link.activity_id, link.text) for link in content.links] == [
        ("activity", 12, "Skript"), ("file", None, "Plan"), ("moodle", None, "Kurs"), ("external", None, "DOI")]
    assert f"[Skript]({BASE}/mod/resource/view.php?id=12)" in content.text
    assert content.text.endswith("Top")


def test_parse_course_page_labels():
    html = LOGGED_IN_PAGE.replace("</body>", """
      <li class="activity label modtype_label" data-id="5"><div class="activity-altcontent">
        <div class="no-overflow"><p>Willkommen im <strong>Kurs</strong></p></div></div></li>
      <li class="activity modtype_resource" data-id="6">
        <img src="https://x/theme/image.php/b/core/1/f/pdf?filtericon=1"></li></body>""")
    page = parse_course_page(html, BASE)
    assert page.labels[5].text == "Willkommen im **Kurs**"
    assert page.icons == {6: "pdf"}


PAGE = LOGGED_IN_PAGE.replace("</body>", """<div role="main">
  <div class="activity-header"><div class="activity-description"><p>Kurz: Regeln</p></div></div>
  <div class="box py-3 generalbox center clearfix"><div class="no-overflow">
    <p>Die Termine stehen im Semesterplan.</p><p><a href="https://doi.org/1">Buch</a></p></div></div>
  <div class="modified">Zuletzt geändert: Montag, 29. September 2025, 10:25</div>
</div></body>""")


def test_parse_page_view():
    content = parse_page_view(PAGE, BASE, TZ)
    assert content.text == ("Kurz: Regeln\n\n---\n\nDie Termine stehen im Semesterplan.\n\n"
                            "[Buch](https://doi.org/1)")
    assert [link.url for link in content.links] == ["https://doi.org/1"]
    assert content.modified_at == datetime(2025, 9, 29, 10, 25, tzinfo=TZ)
    assert parse_page_view(LOGGED_IN_PAGE, BASE, TZ) is None


LONG = "<p>" + "Lorem ipsum " * 200 + "</p>"


@pytest.fixture
def moodle():
    fake = FakeMoodle(
        resources={11: ("Semesterplan", "Semesterplan.pdf", FakeFile(b"plan"))},
        extra_cms=[
            {"id": "50", "name": "Willkommen", "module": "label", "url": "", "visible": True},
            {"id": "51", "name": "Lang", "module": "label", "url": "", "visible": True},
            {"id": "52", "name": "Leer", "module": "label", "url": "", "visible": True},
            {"id": "60", "name": "Hinweise", "module": "page", "url": f"{BASE}/mod/page/view.php?id=60",
             "visible": True},
            {"id": "61", "name": "Gesperrt", "module": "page", "url": "", "visible": True},
            {"id": "70", "name": "Quiz", "module": "quiz", "url": "", "visible": True},
        ],
        labels={50: "<p>Hallo <a href='/mod/page/view.php?id=60'>Hinweise</a></p>", 51: LONG, 52: "<p> </p>"},
        pages={60: PAGE, 61: None},
    )
    with respx.mock(assert_all_called=False) as router:
        fake.mount(router)
        yield fake


@pytest.fixture
async def service(config, store):
    svc = MoodleService(config)
    yield svc
    await svc.aclose()


async def test_course_structure_contains_label_texts(service, moodle):
    structure = await service.get_course(COURSE_ID)
    modules = {m.id: m for m in structure.sections[0].modules}
    assert modules[50].text == f"Hallo [Hinweise]({BASE}/mod/page/view.php?id=60)"
    assert modules[51].text_truncated and modules[51].text.endswith(" …")
    assert len(modules[51].text) <= LABEL_TEXT_LIMIT + 2
    assert modules[52].text is None and not modules[52].text_truncated
    assert modules[60].text is None  # pages are read separately


async def test_get_content_of_label_and_page(service, moodle):
    label = await service.get_content(51)
    assert not label.text.endswith("…") and len(label.text) > LABEL_TEXT_LIMIT
    assert (label.module, label.section, label.course_name) == ("label", "Woche 1", "Test: Kurs")

    linked = await service.get_content(50)
    assert [(link.kind, link.activity_id) for link in linked.links] == [("activity", 60)]

    page = await service.get_content(60)
    assert page.text.startswith("Kurz: Regeln") and page.accessible
    assert page.modified_at == datetime(2025, 9, 29, 10, 25, tzinfo=TZ)
    assert page.url == f"{BASE}/mod/page/view.php?id=60"

    locked = await service.get_content(61)
    assert not locked.accessible and locked.text == "" and locked.note


async def test_get_content_rejects_other_modules(service, moodle):
    with pytest.raises(MoodleError) as exc:
        await service.get_content(70)
    assert exc.value.code == ErrorCode.RESOURCE_NOT_DOWNLOADABLE
    with pytest.raises(MoodleError) as exc:
        await service.get_content(999)
    assert exc.value.code == ErrorCode.RESOURCE_NOT_FOUND


def test_embedded_file_path():
    assert embedded_file_path(f"{BASE}/pluginfile.php/9/mod_page/content/3/Plan%20A.pdf") == "Plan A.pdf"
    assert embedded_file_path(f"{BASE}/pluginfile.php/9/mod_label/intro/Checkliste.pdf") == "Checkliste.pdf"
    url = f"{BASE}/pluginfile.php/9/mod_page/content/3/sub/x.docx?forcedownload=1"
    assert embedded_file_path(url) == "sub/x.docx"
    assert embedded_file_path(f"{BASE}/pluginfile.php/9/mod_page/content/3/../x") is None
    assert embedded_file_path(f"{BASE}/mod/page/view.php?id=1") is None


@pytest.fixture
def embedded_moodle():
    fake = FakeMoodle(
        extra_cms=[
            {"id": "50", "name": "Checkliste", "module": "label", "url": "", "visible": True},
            {"id": "60", "name": "Hinweise: Prüfung", "module": "page", "url": "", "visible": True},
            {"id": "62", "name": "Ohne Dateien", "module": "page", "url": "", "visible": True},
        ],
        labels={50: f"<p><a href='{BASE}/pluginfile.php/7/mod_label/intro/Checkliste%20Team.pdf'>Liste</a></p>"},
        pages={
            60: PAGE.replace("</p></div></div>", f"""</p>
                <a href="{BASE}/pluginfile.php/8/mod_page/content/4/Regeln.pdf">Regeln</a>
                <a href="{BASE}/pluginfile.php/8/mod_page/content/4/Regeln.pdf?forcedownload=1">nochmal</a>
                <a href="{BASE}/pluginfile.php/8/mod_page/content/4/anhang/Vorlage.docx">Vorlage</a>
                <a href="{BASE}/pluginfile.php/8/mod_page/content/4/weg.pdf">kaputt</a>
                </div></div>"""),
            62: PAGE,
        },
        embedded={
            "7/mod_label/intro/Checkliste Team.pdf": FakeFile(b"check"),
            "8/mod_page/content/4/Regeln.pdf": FakeFile(b"regeln"),
            "8/mod_page/content/4/anhang/Vorlage.docx": FakeFile(b"vorlage"),
        },
    )
    with respx.mock(assert_all_called=False) as router:
        fake.mount(router)
        yield fake


async def test_content_links_have_resource_ids(service, embedded_moodle):
    page = await service.get_content(60)
    assert [(link.kind, link.resource_id) for link in page.links if link.kind == "file"] == [
        ("file", "60/Regeln.pdf"), ("file", "60/Regeln.pdf"), ("file", "60/anhang/Vorlage.docx"),
        ("file", "60/weg.pdf")]
    assert all(link.resource_id is None for link in page.links if link.kind != "file")


async def test_download_single_embedded_file(service, embedded_moodle, config):
    result = await service.download_resource("50/Checkliste Team.pdf", None, overwrite=True)
    [f] = result.files
    expected = config.moodle.download_directory / "Test_ Kurs" / "01 Woche 1" / "Checkliste" / "Checkliste Team.pdf"
    assert (f.resource_id, f.path, f.status) == ("50/Checkliste Team.pdf", str(expected), "downloaded")
    assert expected.read_bytes() == b"check"

    again = await service.download_resource("50/Checkliste Team.pdf", None, overwrite=False)
    assert again.files[0].status == "unchanged"


async def test_download_all_files_of_page(service, embedded_moodle, config):
    result = await service.download_resource("60", None, overwrite=True)
    base = config.moodle.download_directory / "Test_ Kurs" / "01 Woche 1" / "Hinweise_ Prüfung"
    assert sorted(f.path for f in result.files) == [str(base / "Regeln.pdf"), str(base / "anhang" / "Vorlage.docx")]
    assert [f.resource_id for f in result.failed] == ["60/weg.pdf"]  # dead link does not stop the rest
    assert (base / "anhang" / "Vorlage.docx").read_bytes() == b"vorlage"


async def test_download_embedded_file_to_destination(service, embedded_moodle, config):
    result = await service.download_resource("60/anhang/Vorlage.docx", "exports", overwrite=True)
    assert result.files[0].path == str(config.moodle.download_directory / "exports" / "anhang" / "Vorlage.docx")
    with pytest.raises(MoodleError) as exc:  # a single requested file that is gone is an error
        await service.download_resource("60/weg.pdf", None, overwrite=True)
    assert exc.value.code == ErrorCode.RESOURCE_NOT_FOUND


async def test_download_embedded_errors(service, embedded_moodle):
    with pytest.raises(MoodleError) as exc:
        await service.download_resource("62", None, overwrite=True)
    assert exc.value.code == ErrorCode.RESOURCE_NOT_DOWNLOADABLE
    with pytest.raises(MoodleError) as exc:
        await service.download_resource("60/nicht-da.pdf", None, overwrite=True)
    assert exc.value.code == ErrorCode.RESOURCE_NOT_FOUND
    with pytest.raises(MoodleError) as exc:
        await service.download_resource("12345/x.pdf", None, overwrite=True)
    assert exc.value.code == ErrorCode.RESOURCE_NOT_FOUND
