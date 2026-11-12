import pytest

from baza_wiedzy.core.config import Settings
from baza_wiedzy.core.events import DOCUMENT_PUBLISHED, EventBus
from baza_wiedzy.core.registry import (
    DuplicatePermissionError,
    MenuItem,
    ModuleDependencyError,
    ModuleError,
    ModuleManifest,
    Permission,
    UnknownModuleError,
    build_registry,
    discover,
    load_ref,
    load_registry,
)


def manifest(name: str, *requires: str, **kwargs: object) -> ModuleManifest:
    return ModuleManifest(name=name, version="1", requires=requires, **kwargs)  # type: ignore[arg-type]


CORE = manifest("core")


def available(*extra: ModuleManifest) -> dict[str, ModuleManifest]:
    return {m.name: m for m in (CORE, *extra)}


# --- real modules -------------------------------------------------------------------------


def test_default_modules_load_in_dependency_order() -> None:
    registry = load_registry(Settings().enabled_modules)
    assert registry.names[0] == "core"
    assert registry.names.index("documents") < registry.names.index("ingestion")
    assert registry.names.index("documents") < registry.names.index("search")


def test_search_without_documents_gives_readable_error() -> None:
    with pytest.raises(ModuleDependencyError) as exc:
        load_registry(["search"])
    message = str(exc.value)
    assert "'search'" in message
    assert "'documents'" in message
    assert "ENABLED_MODULES" in message


def test_documents_alone_is_valid() -> None:
    assert load_registry(["documents"]).names == ("core", "documents")


def test_core_only() -> None:
    assert load_registry([]).names == ("core",)


def test_unknown_module() -> None:
    with pytest.raises(UnknownModuleError, match="nope"):
        load_registry(["nope"])


def test_invalid_module_name_is_rejected_without_importing() -> None:
    with pytest.raises(UnknownModuleError):
        discover("../etc")


def test_permission_catalog_comes_from_manifests() -> None:
    codes = {p.code for p in load_registry(["documents"]).permissions()}
    assert {"documents.read", "documents.publish_any", "categories.manage"} <= codes
    assert {"users.manage", "roles.manage", "audit.view", "system.view"} <= codes


def test_disabled_module_permissions_are_absent() -> None:
    codes = {p.code for p in load_registry([]).permissions()}
    assert "documents.read" not in codes
    assert "users.manage" in codes


def test_catalog_matches_documented_permissions() -> None:
    codes = {p.code for p in load_registry(["documents", "ingestion", "search"]).permissions()}
    assert codes == {
        "documents.read",
        "documents.upload",
        "documents.publish",
        "documents.publish_any",
        "documents.read_drafts",
        "documents.edit_any",
        "documents.delete",
        "categories.manage",
        "users.manage",
        "roles.manage",
        "audit.view",
        "settings.manage",
        "system.view",
    }


def test_menu_is_ordered() -> None:
    labels = [i.label for i in load_registry(["documents", "search"]).menu()]
    assert labels == ["Search", "Documents"]


# --- registry logic with synthetic manifests ----------------------------------------------


def test_cycle_is_reported() -> None:
    with pytest.raises(ModuleDependencyError, match="Circular"):
        build_registry(["a", "b"], available(manifest("a", "b"), manifest("b", "a")))


def test_all_missing_dependencies_are_listed() -> None:
    with pytest.raises(ModuleDependencyError) as exc:
        build_registry(["a"], available(manifest("a", "x", "y"), manifest("x"), manifest("y")))
    assert "'x'" in str(exc.value) and "'y'" in str(exc.value)


def test_duplicate_permission_codes_are_rejected() -> None:
    perm = Permission("x.do", "d")
    mods = available(manifest("a", permissions=(perm,)), manifest("b", permissions=(perm,)))
    with pytest.raises(DuplicatePermissionError):
        build_registry(["a", "b"], mods).permissions()


def test_core_listed_explicitly_is_not_duplicated() -> None:
    assert build_registry(["core", "a"], available(manifest("a"))).names == ("core", "a")


def test_invalid_manifest_name() -> None:
    with pytest.raises(ModuleError):
        ModuleManifest(name="Bad Name", version="1")


def test_menu_item_default_order() -> None:
    assert MenuItem(label="x", path="/x").order == 100


def test_load_ref() -> None:
    assert load_ref("baza_wiedzy.core.registry:CORE_NAME") == "core"
    with pytest.raises(ModuleError):
        load_ref("not-a-ref")


# --- settings -----------------------------------------------------------------------------


def test_enabled_modules_parsed_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLED_MODULES", " documents, Search ,,documents,core")
    assert Settings(_env_file=None).enabled_modules == ("documents", "search")  # type: ignore[call-arg]


def test_enabled_modules_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENABLED_MODULES", raising=False)
    assert Settings(_env_file=None).enabled_modules == ("documents", "ingestion", "search")  # type: ignore[call-arg]


# --- event bus ----------------------------------------------------------------------------


def test_event_bus_delivers_in_order() -> None:
    bus = EventBus()
    seen: list[str] = []
    bus.subscribe(DOCUMENT_PUBLISHED, lambda p: seen.append("first:" + p["document_id"]))
    bus.subscribe(DOCUMENT_PUBLISHED, lambda p: seen.append("second:" + p["document_id"]))
    bus.publish(DOCUMENT_PUBLISHED, {"document_id": "d1", "version_id": "v1"})
    assert seen == ["first:d1", "second:d1"]


def test_event_without_subscribers_is_ignored() -> None:
    EventBus().publish("nothing.listens", {})


def test_modules_subscribe_through_on_register() -> None:
    received: list[str] = []

    def on_register(bus: EventBus) -> None:
        bus.subscribe("ping", lambda p: received.append(p["x"]))

    registry = build_registry(["a"], available(manifest("a", on_register=on_register)))
    bus = EventBus()
    registry.register_events(bus)
    bus.publish("ping", {"x": "ok"})
    assert received == ["ok"]
