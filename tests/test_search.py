import sqlite3

from zhaw_moodle_mcp.models import Course
from zhaw_moodle_mcp.search import search
from zhaw_moodle_mcp.sync.database import Database, IndexedModule, IndexedSection


def row(kind, name, section="", course="Wirtschaftsrecht", type_="pdf", id_="1"):
    return {"kind": kind, "id": id_, "name": name, "course_id": 1, "course_name": course,
            "section": section, "type": type_, "url": None, "downloadable": 1}


ROWS = [
    row("module", "OR_ZGB_Einführung", "SW 1", id_="1"),
    row("module", "Übung 3: Vertragsrecht", "SW 3", id_="2"),
    row("module", "Uebungen Teil 2", "SW 4", id_="3"),
    row("module", "UML: Klassendiagramm", "SW 2", course="Software Engineering 1", id_="4"),
    row("section", "Obligationenrecht (OR)", "Obligationenrecht (OR)", type_="section", id_="5"),
    row("course", "Wirtschaftsrecht", "WR", type_="course", id_="6"),
    row("file", "Material/Semesterprogramm.pdf", "Allgemein", id_="90/Material/Semesterprogramm.pdf"),
]


def names(query, **kw):
    return [r["name"] for _, r in search(query, ROWS, **kw)]


def test_all_words_must_match():
    assert names("OR ZGB") == ["OR_ZGB_Einführung"]
    assert names("ZGB Vertragsrecht") == []


def test_umlaut_variants():
    assert names("Übung 3")[0] == "Übung 3: Vertragsrecht"
    assert "Übung 3: Vertragsrecht" in names("ubung")
    assert "Uebungen Teil 2" in names("übungen")
    assert "OR_ZGB_Einführung" in names("einfuehrung")


def test_prefix_and_typo():
    assert names("Semesterprog") == ["Material/Semesterprogramm.pdf"]
    assert names("Klassendiagram") == ["UML: Klassendiagramm"]
    assert names("Klasendiagramm") == ["UML: Klassendiagramm"]


def test_context_match_scores_lower_than_name_match():
    results = search("Obligationenrecht", ROWS)
    assert results[0][1]["kind"] == "section"


def test_course_name_as_context():
    assert "UML: Klassendiagramm" in names("Software UML")


def test_kinds_filter_and_limit():
    assert names("recht", kinds={"course"}) == ["Wirtschaftsrecht"]
    assert len(search("recht", ROWS, limit=1)) == 1
    assert names("   ") == []


def test_index_course_and_search_rows(tmp_path):
    db = Database(tmp_path / "m.db")
    course = Course(id=7, name="Wirtschaftsrecht", short_name="WR", url="u", status="active")
    sections = [IndexedSection(id=1, number=0, title="Allgemein", path="Allgemein")]
    modules = [
        IndexedModule(id=11, section="Allgemein", name="Skript", module="resource", type="pdf", url="u11",
                      downloadable=True),
        IndexedModule(id=12, section="Allgemein", name="Info", module="label", type="label", url=None,
                      downloadable=False),
    ]
    db.index_course(course, sections, modules)
    first_seen = db.module_first_seen(7)
    indexed_at, first_indexed = db.course_index_times(7)
    assert indexed_at == first_indexed and set(first_seen) == {11, 12}

    db.index_course(course, sections, modules[:1])  # label removed
    assert db.course_index_times(7)[1] == first_indexed
    assert db.module_first_seen(7)[11] == first_seen[11]
    rows = db.search_rows([7])
    assert sorted((r["kind"], r["name"]) for r in rows) == [
        ("course", "Wirtschaftsrecht"), ("module", "Skript"), ("section", "Allgemein")]
    assert db.search_rows([8]) == []


def test_migrates_v1_database(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE courses (id INTEGER PRIMARY KEY, name TEXT NOT NULL, short_name TEXT, "
                 "status TEXT, last_synced_at TEXT)")
    conn.execute("INSERT INTO courses VALUES (1, 'A', 'a', 'active', NULL)")
    conn.commit()
    conn.close()
    db = Database(path)
    assert db.course_index_times(1) == (None, None)
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == 2
