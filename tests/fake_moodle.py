"""A minimal in-memory Moodle served through respx."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx
import respx

from .conftest import BASE, GUEST_PAGE, LOGGED_IN_PAGE

COURSE_ID = 7


@dataclass
class FakeFile:
    content: bytes
    revision: int = 1
    content_type: str = "application/pdf"

    @property
    def etag(self) -> str:
        return f'"{hash(self.content) & 0xFFFFFFFF:x}"'


@dataclass
class FakeMoodle:
    logged_in: bool = True
    # cmid -> (name, filename, file)
    resources: dict[int, tuple[str, str, FakeFile]] = field(default_factory=dict)
    # folder files: path -> file
    folder: dict[str, FakeFile] = field(default_factory=dict)
    views: int = 0  # mod/resource/view.php hits
    downloads: int = 0

    def resource_url(self, cmid: int) -> str:
        _, filename, f = self.resources[cmid]
        return f"{BASE}/pluginfile.php/{cmid}/mod_resource/content/{f.revision}/{filename}"

    # --- handlers -----------------------------------------------------------

    def my(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, html=LOGGED_IN_PAGE if self.logged_in else GUEST_PAGE)

    def ajax(self, request: httpx.Request) -> httpx.Response:
        if not self.logged_in:
            return httpx.Response(200, json={"error": "x", "errorcode": "servicerequireslogin"})
        call = json.loads(request.content)[0]
        method = call["methodname"]
        if method == "core_course_get_enrolled_courses_by_timeline_classification":
            courses = []
            if call["args"]["classification"] == "inprogress":
                courses = [{"id": COURSE_ID, "fullname": "Test: Kurs", "shortname": "TK",
                            "viewurl": f"{BASE}/course/view.php?id=7"}]
            return httpx.Response(200, json=[{"error": False, "data": {"courses": courses}}])
        if method == "core_courseformat_get_state":
            if call["args"]["courseid"] != COURSE_ID:
                return httpx.Response(200, json=[{"error": True, "exception": {"errorcode": "invalidrecord"}}])
            cms = [{"id": str(cmid), "name": name, "module": "resource", "url": "", "visible": True}
                   for cmid, (name, _, _) in self.resources.items()]
            cms.append({"id": "90", "name": "Material", "module": "folder", "url": "", "visible": True})
            state = {"section": [{"id": "1", "number": 1, "title": "Woche 1",
                                  "cmlist": [c["id"] for c in cms]}], "cm": cms}
            return httpx.Response(200, json=[{"error": False, "data": json.dumps(state)}])
        return httpx.Response(200, json=[{"error": True, "exception": {"errorcode": "servicenotavailable"}}])

    def course_view(self, request: httpx.Request) -> httpx.Response:
        if not self.logged_in:
            return httpx.Response(303, headers={"location": f"{BASE}/login/index.php"})
        items = "".join(
            f'<li class="activity modtype_resource" data-id="{cmid}">'
            f'<img src="{BASE}/theme/image.php/x/core/1/f/pdf?filtericon=1"></li>'
            for cmid in self.resources
        )
        return httpx.Response(200, html=LOGGED_IN_PAGE.replace("</body>", f"<ul>{items}</ul></body>"))

    def folder_view(self, request: httpx.Request) -> httpx.Response:
        links = "".join(f'<a href="{BASE}/pluginfile.php/90/mod_folder/content/0/{p}?forcedownload=1">{p}</a>'
                        for p in self.folder)
        return httpx.Response(200, html=LOGGED_IN_PAGE.replace("</body>", links + "</body>"))

    def resource_view(self, request: httpx.Request) -> httpx.Response:
        if not self.logged_in:
            return httpx.Response(303, headers={"location": f"{BASE}/login/index.php"})
        self.views += 1
        cmid = int(request.url.params["id"])
        if cmid not in self.resources:
            return httpx.Response(404)
        return httpx.Response(303, headers={"location": self.resource_url(cmid)})

    def pluginfile(self, request: httpx.Request) -> httpx.Response:
        if not self.logged_in:
            return httpx.Response(303, headers={"location": f"{BASE}/login/index.php"})
        parts = request.url.path.split("/")
        f: FakeFile | None = None
        if parts[3] == "mod_resource":
            cmid = int(parts[2])
            if cmid in self.resources:
                _, filename, candidate = self.resources[cmid]
                if parts[-1] == filename:  # revision is ignored, like Moodle does
                    f = candidate
        else:
            f = self.folder.get("/".join(parts[6:]))
        if f is None:
            return httpx.Response(404)
        headers = {"etag": f.etag, "content-type": f.content_type,
                   "last-modified": f"rev {f.revision}"}
        if request.method == "HEAD":
            return httpx.Response(200, headers=headers)
        self.downloads += 1
        if f.content_type == "text/html":  # Apache mod_deflate marks compressed responses
            headers["etag"] = headers["etag"][:-1] + '-gzip"'
        return httpx.Response(200, headers=headers, content=f.content)

    def mount(self, router: respx.MockRouter) -> None:
        router.get(f"{BASE}/my/").mock(side_effect=self.my)
        router.post(f"{BASE}/lib/ajax/service.php").mock(side_effect=self.ajax)
        router.get(f"{BASE}/course/view.php").mock(side_effect=self.course_view)
        router.get(f"{BASE}/mod/folder/view.php").mock(side_effect=self.folder_view)
        router.get(f"{BASE}/mod/resource/view.php").mock(side_effect=self.resource_view)
        router.route(url__startswith=f"{BASE}/pluginfile.php/").mock(side_effect=self.pluginfile)
        router.get(f"{BASE}/login/logout.php").mock(return_value=httpx.Response(303))
