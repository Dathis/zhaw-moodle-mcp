from pathlib import Path

import pytest
import respx

from zhaw_moodle_mcp.errors import ErrorCode, MoodleError, SessionExpired
from zhaw_moodle_mcp.service import MoodleService

from .fake_moodle import COURSE_ID, FakeFile, FakeMoodle


@pytest.fixture
def moodle():
    fake = FakeMoodle(
        resources={
            11: ("Semesterplan", "Semesterplan.pdf", FakeFile(b"plan v1")),
            12: ("OR: Einführung", "OR_Einf.pdf", FakeFile(b"or v1")),
        },
        folder={"Blatt1.pdf": FakeFile(b"b1"), "sub/Blatt2.pdf": FakeFile(b"b2")},
    )
    with respx.mock(assert_all_called=False) as router:
        fake.mount(router)
        yield fake


@pytest.fixture
async def service(config, store):
    svc = MoodleService(config)
    yield svc
    await svc.aclose()


def course_dir(config) -> Path:
    return config.moodle.download_directory / "Test_ Kurs"


async def test_auth_status(service, moodle):
    assert (await service.auth_status()).state == "valid"
    moodle.logged_in = False
    assert (await service.auth_status()).state == "expired"


async def test_auth_status_missing(config):
    svc = MoodleService(config)
    try:
        assert (await svc.auth_status()).state == "missing"
    finally:
        await svc.aclose()


async def test_list_courses_and_structure(service, moodle):
    courses = await service.list_courses("active")
    assert [c.name for c in courses] == ["Test: Kurs"]
    assert await service.list_courses("past") == []
    structure = await service.get_course(COURSE_ID)
    modules = structure.sections[0].modules
    assert [m.type for m in modules] == ["pdf", "pdf", "folder"]


async def test_unknown_course(service, moodle):
    with pytest.raises(MoodleError) as exc:
        await service.get_course(999)
    assert exc.value.code == ErrorCode.COURSE_NOT_FOUND


async def test_list_resources_with_folder(service, moodle):
    result = await service.list_resources(COURSE_ID, include_folder_contents=True)
    ids = [r.id for r in result.resources]
    assert ids == ["11", "12", "90", "90/Blatt1.pdf", "90/sub/Blatt2.pdf"]
    assert result.resources[4].folder_id == 90


async def test_download_resource_default_path(service, moodle, config):
    result = await service.download_resource("12", None, overwrite=True)
    path = Path(result.files[0].path)
    assert path == course_dir(config) / "01 Woche 1" / "OR_Einf.pdf"
    assert path.read_bytes() == b"or v1"
    assert not list(path.parent.glob("*.part"))


async def test_download_folder_to_destination(service, moodle, config):
    result = await service.download_resource("90", "custom", overwrite=True)
    paths = sorted(Path(f.path) for f in result.files)
    base = config.moodle.download_directory / "custom"
    assert paths == [base / "Blatt1.pdf", base / "sub" / "Blatt2.pdf"]
    again = await service.download_resource("90", "custom", overwrite=False)
    assert {f.status for f in again.files} == {"unchanged"}


async def test_download_non_file(service, moodle):
    with pytest.raises(MoodleError) as exc:
        await service.download_resource("999", None, overwrite=True)
    assert exc.value.code == ErrorCode.RESOURCE_NOT_FOUND


async def test_sync_lifecycle(service, moodle, config):
    # first sync: everything new
    first = await service.sync_course(COURSE_ID, dry_run=False, update_changed=True)
    assert len(first.new) == 4 and first.unchanged == 0 and not first.failed
    folder_file = course_dir(config) / "01 Woche 1" / "Material" / "sub" / "Blatt2.pdf"
    assert folder_file.read_bytes() == b"b2"

    # second sync: nothing changed, nothing downloaded, no view.php hits
    views, downloads = moodle.views, moodle.downloads
    second = await service.sync_course(COURSE_ID, dry_run=False, update_changed=True)
    assert second.unchanged == 4 and not (second.new or second.updated)
    assert (moodle.views, moodle.downloads) == (views, downloads)

    # file replaced (same name, new revision), one renamed, one deleted locally, one removed
    moodle.resources[11] = ("Semesterplan", "Semesterplan.pdf", FakeFile(b"plan v2", revision=2))
    moodle.resources[12] = ("OR Einführung (neu)", "OR_Einf.pdf", moodle.resources[12][2])
    folder_file.unlink()
    del moodle.folder["Blatt1.pdf"]

    dry = await service.sync_course(COURSE_ID, dry_run=True, update_changed=True)
    assert [c.resource_id for c in dry.updated] == ["11"]
    assert (course_dir(config) / "01 Woche 1" / "Semesterplan.pdf").read_bytes() == b"plan v1"

    third = await service.sync_course(COURSE_ID, dry_run=False, update_changed=True)
    assert [c.resource_id for c in third.updated] == ["11"]
    assert [c.resource_id for c in third.restored] == ["90/sub/Blatt2.pdf"]
    assert [c.resource_id for c in third.renamed] == ["12"]
    assert [c.resource_id for c in third.removed] == ["90/Blatt1.pdf"]
    assert (course_dir(config) / "01 Woche 1" / "Semesterplan.pdf").read_bytes() == b"plan v2"
    assert folder_file.exists()

    fourth = await service.sync_course(COURSE_ID, dry_run=False, update_changed=True)
    assert fourth.unchanged == 3 and not (fourth.updated or fourth.removed or fourth.renamed)


async def test_sync_renamed_file_on_moodle(service, moodle, config):
    await service.sync_course(COURSE_ID, dry_run=False, update_changed=True)
    # uploader replaced the file with a differently named one -> old pluginfile URL is 404
    moodle.resources[11] = ("Semesterplan", "Semesterplan_v2.pdf", FakeFile(b"plan v2", revision=2))
    result = await service.sync_course(COURSE_ID, dry_run=False, update_changed=True)
    assert [c.resource_id for c in result.updated] == ["11"]
    assert (course_dir(config) / "01 Woche 1" / "Semesterplan_v2.pdf").read_bytes() == b"plan v2"


async def test_expired_session_without_auto_login(service, moodle):
    moodle.logged_in = False
    with pytest.raises(SessionExpired):
        await service.list_courses(None)


async def test_relogin_and_retry(service, moodle, monkeypatch):
    service.config.browser.auto_login = True
    logins = []

    async def fake_login(base_url, browser, store):
        logins.append(base_url)
        moodle.logged_in = True

    monkeypatch.setattr("zhaw_moodle_mcp.service.interactive_login", fake_login)
    await service.list_courses(None)  # validates the session
    moodle.logged_in = False  # expires while the validation is still cached
    courses = await service.list_courses(None)
    assert [c.id for c in courses] == [COURSE_ID]
    assert len(logins) == 1


async def test_logout_clears_local_state(service, moodle, config):
    config.browser.profile_directory.mkdir(parents=True)
    result = await service.logout()
    assert result.logged_out and result.server_session_invalidated
    assert not config.session.storage_state.exists()
    assert not config.browser.profile_directory.exists()
    assert (await service.auth_status()).state == "missing"


async def test_html_course_file_is_not_a_session_problem(service, moodle, config):
    moodle.resources[13] = ("Player", "player.html", FakeFile(b"<html>quiz</html>", content_type="text/html"))
    first = await service.sync_course(COURSE_ID, dry_run=False, update_changed=True)
    assert "13" in [c.resource_id for c in first.new] and not first.failed
    second = await service.sync_course(COURSE_ID, dry_run=False, update_changed=True)
    assert second.unchanged == 5


async def test_session_expires_during_sync_relogin(service, moodle, monkeypatch, config):
    service.config.browser.auto_login = True

    async def fake_login(base_url, browser, store):
        moodle.logged_in = True

    monkeypatch.setattr("zhaw_moodle_mcp.service.interactive_login", fake_login)
    original = moodle.pluginfile

    def expire_once(request):
        if moodle.downloads == 1 and not getattr(moodle, "expired", False):
            moodle.expired = True
            moodle.logged_in = False
        return original(request)

    moodle.pluginfile = expire_once
    with respx.mock(assert_all_called=False) as router:
        moodle.mount(router)
        result = await service.sync_course(COURSE_ID, dry_run=False, update_changed=True)
    assert not result.failed
    assert len(list((course_dir(config) / "01 Woche 1").rglob("*.pdf"))) == 4
