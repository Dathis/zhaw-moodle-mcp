from zhaw_moodle_mcp.models import Course
from zhaw_moodle_mcp.moodle.courses import build_structure, course_from_api
from zhaw_moodle_mcp.moodle.resources import entries_from_structure

COURSE = Course(id=7, name="Wirtschaftsrecht (2026-HS)", short_name="WR", url="u", status="active")

STATE = {
    "section": [
        {"id": "100", "number": 0, "title": "Allgemein", "cmlist": ["1", "2"], "visible": True},
        {"id": "101", "number": 1, "title": "SW 1 &amp; 2", "cmlist": ["3", "4", "5", "6"], "visible": True},
        {"id": "150", "number": 15, "title": "Übungen", "cmlist": ["7", "8"], "component": "mod_subsection",
         "itemid": 1, "parentsectionid": "101", "visible": True},
    ],
    "cm": [
        {"id": "1", "name": "Forum", "module": "forum", "url": "f", "visible": True, "uservisible": True},
        {"id": "2", "name": "Semesterplan", "module": "resource", "url": "r2", "visible": True,
         "uservisible": True},
        {"id": "3", "name": "Slides", "module": "folder", "url": "f3", "visible": True, "uservisible": True},
        {"id": "4", "name": "Gesetz", "module": "url", "url": "u4", "visible": True, "uservisible": True},
        {"id": "5", "name": "Übungen", "module": "subsection", "url": None, "visible": True,
         "uservisible": True},
        {"id": "6", "name": "Quiz", "module": "quiz", "url": "q6", "visible": True, "uservisible": False},
        {"id": "7", "name": "Blatt 1", "module": "resource", "url": "r7", "visible": True,
         "uservisible": True},
        {"id": "8", "name": "Locked", "module": "resource", "url": "r8", "visible": True,
         "uservisible": False},
    ],
}


def test_course_from_api():
    c = course_from_api({"id": 5, "fullname": "A &amp; B", "shortname": "AB", "viewurl": "v",
                         "startdate": 1700000000, "enddate": 0, "isfavourite": True}, "past")
    assert c.name == "A & B" and c.status == "past" and c.start_date and c.end_date is None and c.favourite


def test_build_structure_nests_subsections():
    s = build_structure(COURSE, STATE, {2: "pdf", 7: "document"})
    assert [x.title for x in s.sections] == ["Allgemein", "SW 1 & 2"]
    sw1 = s.sections[1]
    assert [m.id for m in sw1.modules] == [3, 4, 6]  # subsection cm is not a module
    assert [x.title for x in sw1.subsections] == ["Übungen"]
    assert sw1.subsections[0].modules[0].type == "word"
    assert s.sections[0].modules[1].type == "pdf"
    assert sw1.modules[0].type == "folder" and sw1.modules[0].downloadable
    assert sw1.modules[1].type == "link" and not sw1.modules[1].downloadable
    assert not sw1.modules[2].visible


def test_entries_paths():
    entries = {e.id: e for e in entries_from_structure(build_structure(COURSE, STATE, {}))}
    assert set(entries) == {"2", "3", "4", "7", "8"}  # forum and quiz are not resources
    assert entries["7"].resource.section == "SW 1 & 2 / Übungen"
    assert entries["7"].section_dirs == ["01 SW 1 & 2", "Übungen"]
    assert entries["2"].is_file and not entries["3"].is_file
    assert not entries["8"].resource.downloadable and not entries["8"].is_file
