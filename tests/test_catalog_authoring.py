"""Catalog renames preserve the source until the replacement is saved."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from torque.commands.catalog import handle_catalog_command
from torque.roles import RoleManager
from torque.specializations import SpecializationManager


class CatalogAuthoringTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        self.env = patch.dict(os.environ, {"HOME": str(self.home)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.roles = RoleManager()
        self.specs = SpecializationManager()

        async def resolve(_group):
            return str(self.project)

        self.runtime = SimpleNamespace(
            state=None, db=None, action_mgr=None, template_mgr=self.roles,
            specialization_mgr=self.specs, resolve_base_dir=resolve,
            handle_set_engineer_specializations_command=None, action_to_yaml=None,
        )

    def save(self, kind, name, scope, data):
        fn = self.specs.save_specialization if kind == "specialization" else self.roles.save_role
        return fn(name, data, scope=scope, base_dir=str(self.project))

    def load(self, kind, name, scope):
        fn = self.specs.get_specialization if kind == "specialization" else self.roles.load_role
        return fn(name, base_dir=str(self.project), scope=scope)

    async def command(self, kind, **args):
        return await handle_catalog_command({"cmd": f"save_{kind}", "group": "g", **args}, self.runtime)

    async def test_invalid_rename_keeps_both_scopes_and_never_creates_replacement(self):
        for kind in ("role", "template", "specialization"):
            with self.subTest(kind=kind):
                self.save(kind, "original", "project", {"preamble": "project"})
                self.save(kind, "original", "user", {"preamble": "user"})
                result = await self.command(kind, name="replacement", old_name="original", old_scope="project", scope="project", data={"invalid_listing_field": True})
                self.assertEqual(result["type"], "error")
                self.assertEqual(self.load(kind, "original", "project")["preamble"], "project")
                self.assertEqual(self.load(kind, "original", "user")["preamble"], "user")
                self.assertIsNone(self.load(kind, "replacement", "project"))

    async def test_scoped_rename_and_move_preserve_shadowed_definition(self):
        for kind in ("role", "template", "specialization"):
            with self.subTest(kind=kind):
                name = f"entry-{kind}"
                self.save(kind, name, "project", {"preamble": "project"})
                self.save(kind, name, "user", {"preamble": "user"})
                result = await self.command(kind, name=name + "-renamed", old_name=name, old_scope="user", scope="user", data={"preamble": "renamed"})
                self.assertEqual(result["saved"], name + "-renamed")
                self.assertEqual(self.load(kind, name, "project")["preamble"], "project")
                self.assertIsNone(self.load(kind, name, "user"))
                result = await self.command(kind, name=name + "-renamed", old_name=name + "-renamed", old_scope="user", scope="project", data={"preamble": "moved"})
                self.assertEqual(result["saved"], name + "-renamed")
                self.assertIsNone(self.load(kind, name + "-renamed", "user"))
                self.assertEqual(self.load(kind, name + "-renamed", "project")["preamble"], "moved")

    async def test_legacy_rename_without_scope_removes_only_resolved_original(self):
        for kind in ("role", "template", "specialization"):
            with self.subTest(kind=kind):
                self.save(kind, "legacy", "project", {"preamble": "project"})
                self.save(kind, "legacy", "user", {"preamble": "user"})
                result = await self.command(kind, name="legacy-new", old_name="legacy", scope="project", data={"preamble": "new"})
                self.assertEqual(result["saved"], "legacy-new")
                self.assertIsNone(self.load(kind, "legacy", "project"))
                self.assertEqual(self.load(kind, "legacy", "user")["preamble"], "user")

    async def test_non_object_payload_does_not_remove_original(self):
        for kind in ("role", "template", "specialization"):
            with self.subTest(kind=kind):
                self.save(kind, "safe", "project", {"preamble": "keep"})
                result = await self.command(kind, name="invalid", old_name="safe", old_scope="project", scope="project", data=[])
                self.assertEqual(result["type"], "error")
                self.assertEqual(self.load(kind, "safe", "project")["preamble"], "keep")

    async def test_empty_destination_scope_means_project_and_invalid_scope_preserves_source(self):
        for kind in ("role", "template", "specialization"):
            with self.subTest(kind=kind):
                self.save(kind, "scope-entry", "project", {"preamble": "keep"})
                for scope in (None, ""):
                    result = await self.command(kind, name="scope-entry", old_name="scope-entry", old_scope="project", scope=scope, data={"preamble": "keep"})
                    self.assertEqual(result["saved"], "scope-entry")
                    self.assertEqual(self.load(kind, "scope-entry", "project")["preamble"], "keep")
                result = await self.command(kind, name="scope-entry", old_name="scope-entry", old_scope="project", scope="invalid", data={"preamble": "changed"})
                self.assertEqual(result["type"], "error")
                self.assertEqual(self.load(kind, "scope-entry", "project")["preamble"], "keep")
