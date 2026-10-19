"""Run the in-memory pipeline on PHP snippets (shared by the later-stage tests)."""

from __future__ import annotations

from c1.astdoc import AstDoc
from c1.cfg import build_project_cfgs
from c1.concerns import build_labels
from c1.dfg import build_project_dfg
from c1.includes import build_includes
from conftest import php_ast


class Run:
    def __init__(self, files: dict[str, str] | str):
        if isinstance(files, str):
            files = {"p.php": files}
        self.files = files
        paths = sorted(files, key=lambda p: p.encode("utf-8"))
        self.asts = [AstDoc(php_ast(files[p], f"f{i:03d}", p)) for i, p in enumerate(paths)]
        self.by_path = {a.path: a for a in self.asts}
        self.manifest = {"files": [{"path": p, "fileId": f"f{i:03d}"} for i, p in enumerate(paths)],
                         "excluded": [], "contentHash": "test"}
        self.cfgs, self.cfg_summary = build_project_cfgs(self.asts)
        self.includes = build_includes(self.asts, self.cfgs, self.manifest)
        self.project = build_project_dfg(self.asts, self.cfgs, self.includes)
        self.dfgs = [self.project.file_doc(a.file_id) for a in self.asts]
        self.labels = build_labels(self.asts, self.cfgs, self.dfgs, "test")
        self._auth = None

    @property
    def auth(self):
        if self._auth is None:
            from c1.auth import analyse as auth_analyse  # lazy: commit group 5 has no auth.py yet

            src = {a.file_id: self.files[a.path].encode("utf-8") for a in self.asts}
            self._auth = auth_analyse(self.asts, self.cfgs, self.includes, self.dfgs, self.labels, src)
        return self._auth

    def text(self, nid: str) -> str:
        a = self.asts[int(nid[1:4])]
        loc = a.loc(nid)
        return self.files[a.path].encode("utf-8")[loc["startByte"]:loc["endByte"]].decode("utf-8")

    def label_of(self, needle: str, kind: str | None = None, path: str = "p.php") -> dict:
        """Label of the first labelled node in `path` whose source starts with `needle`."""
        a = self.by_path[path]
        for nid, lab in sorted(self.labels["labels"].items()):
            if not nid.startswith(a.file_id + "#"):
                continue
            if kind and a.kind(nid) != kind:
                continue
            if self.text(nid).startswith(needle):
                return lab
        raise AssertionError(f"no labelled node starting with {needle!r}")

    def page(self, path: str) -> dict:
        return next(p for p in self.auth["pages"] if p["page"] == path)
