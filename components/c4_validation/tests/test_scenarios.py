"""Reading an endpoint map and resolving scenarios against it."""

import copy
import json
from pathlib import Path

import pytest

from c4.config import load_config
from c4.scenarios import (
    EndpointMapError,
    ScenarioError,
    load_all,
    load_endpoint_map,
    resolve,
)

MAP = {
    "format": "c4-stand-in/1",
    "endpoints": [
        {
            "endpoint_id": "GET /api/things",
            "method": "GET",
            "path": "/api/things",
            "records_at": "things",
            "sources": [
                {
                    "source_id": "search",
                    "legacy": {
                        "method": "POST", "path": "/search.php",
                        "fixed": {"search_submit": "1"}, "records": "only",
                    },
                    "parameters": [
                        {"name": "q", "in": "query", "legacy_name": "search_text",
                         "legacy_in": "body", "column": "things.name"},
                    ],
                },
                {
                    "source_id": "list",
                    "legacy": {
                        "method": "GET", "path": "/panel.php",
                        "fixed": {"tab": "things"}, "records": {"table": 2},
                    },
                    "parameters": [],
                },
            ],
        },
        {
            "endpoint_id": "PUT /api/things/{id}",
            "method": "PUT",
            "path": "/api/things/{id}",
            "records_at": None,
            "sources": [
                {
                    "source_id": "edit_form",
                    "legacy": {"method": "POST", "path": "/edit.php", "fixed": {"save": "1"}, "records": "none"},
                    "parameters": [
                        {"name": "id", "in": "path", "legacy_name": "thing_id", "legacy_in": "query"},
                        {"name": "name", "in": "body", "legacy_name": "txtName", "legacy_in": "body"},
                        {"name": "fee", "in": "body", "legacy_name": "txtFee", "legacy_in": "body"},
                    ],
                },
            ],
        },
    ],
}


def write_map(tmp_path: Path, document=MAP) -> Path:
    file = tmp_path / "endpoint_map.json"
    file.write_text(json.dumps(document) if not isinstance(document, str) else document)
    return file


@pytest.fixture
def endpoint_map(tmp_path):
    return load_endpoint_map(write_map(tmp_path))


def scenario(endpoint="GET /api/things", source="search", **values):
    return {
        "scenario_id": "s_01", "scenario_type": "typical",
        "endpoint_id": endpoint, "source_id": source, "values": values,
    }


def changed(change):
    document = copy.deepcopy(MAP)
    change(document)
    return document


# ------------------------------------------------------------- reading the map


def test_an_endpoint_and_the_pages_behind_it_are_read(endpoint_map):
    endpoint = endpoint_map["GET /api/things"]

    assert (endpoint.method, endpoint.path, endpoint.records_at) == ("GET", "/api/things", "things")
    assert list(endpoint.sources) == ["search", "list"]
    search = endpoint.sources["search"]
    assert (search.legacy_method, search.legacy_path) == ("POST", "/search.php")
    assert search.fixed == {"search_submit": "1"}
    (parameter,) = search.parameters
    assert (parameter.name, parameter.location) == ("q", "query")
    assert (parameter.legacy_name, parameter.legacy_location) == ("search_text", "body")
    assert parameter.column == "things.name"


def test_where_the_records_are_can_be_said_three_ways(endpoint_map):
    assert endpoint_map["GET /api/things"].sources["search"].records == "only"
    assert endpoint_map["GET /api/things"].sources["list"].records == 2
    assert endpoint_map["PUT /api/things/{id}"].sources["edit_form"].records == "none"


def test_a_parameter_need_not_name_a_column(endpoint_map):
    parameters = endpoint_map["PUT /api/things/{id}"].sources["edit_form"].parameters

    assert [parameter.column for parameter in parameters] == [None, None, None]


def test_a_missing_file_is_reported(tmp_path):
    with pytest.raises(EndpointMapError, match="No endpoint map"):
        load_endpoint_map(tmp_path / "endpoint_map.json")


def test_a_file_that_is_not_json_is_reported(tmp_path):
    with pytest.raises(EndpointMapError, match="not valid JSON"):
        load_endpoint_map(write_map(tmp_path, "{ this is not json"))


@pytest.mark.parametrize("document", [{"endpoints": []}, {"format": "openapi/3.1", "paths": {}}, []])
def test_a_map_in_a_format_with_no_reader_is_refused_by_name(tmp_path, document):
    with pytest.raises(EndpointMapError, match="no known format. Known: c4-stand-in/1"):
        load_endpoint_map(write_map(tmp_path, document))


def test_an_endpoint_listed_twice_is_refused(tmp_path):
    document = changed(lambda d: d["endpoints"].append(d["endpoints"][0]))

    with pytest.raises(EndpointMapError, match="'GET /api/things' is listed twice"):
        load_endpoint_map(write_map(tmp_path, document))


def test_a_missing_part_of_an_endpoint_is_named(tmp_path):
    document = changed(lambda d: d["endpoints"][0].pop("records_at"))

    with pytest.raises(EndpointMapError, match="does not set: records_at"):
        load_endpoint_map(write_map(tmp_path, document))


def test_a_source_that_does_not_say_where_its_records_are_is_refused(tmp_path):
    document = changed(lambda d: d["endpoints"][0]["sources"][0]["legacy"].update(records="second table"))

    with pytest.raises(EndpointMapError, match="must say where its records are"):
        load_endpoint_map(write_map(tmp_path, document))


def test_a_parameter_in_an_unknown_place_is_refused(tmp_path):
    document = changed(lambda d: d["endpoints"][0]["sources"][0]["parameters"][0].update({"in": "cookie"}))

    with pytest.raises(EndpointMapError, match="'q' of 'GET /api/things' has an unknown location"):
        load_endpoint_map(write_map(tmp_path, document))


# ---------------------------------------------------------- resolving a scenario


def test_one_value_reaches_each_system_under_that_systems_own_name(endpoint_map):
    resolved = resolve(endpoint_map, scenario(q="laptop"))

    assert resolved["legacy_request"] == {
        "method": "POST", "path": "/search.php", "params": {},
        "body": {"search_submit": "1", "search_text": "laptop"},
    }
    assert resolved["migrated_request"] == {
        "method": "GET", "path": "/api/things", "params": {"q": "laptop"},
        "body": None, "records_at": "things",
    }


def test_scenario_identity_is_carried_through(endpoint_map):
    resolved = resolve(endpoint_map, scenario(q="laptop"))

    assert (resolved["scenario_id"], resolved["scenario_type"], resolved["endpoint_id"]) == (
        "s_01", "typical", "GET /api/things",
    )


def test_fixed_fields_travel_where_the_legacy_method_carries_data(endpoint_map):
    resolved = resolve(endpoint_map, scenario(source="list"))

    assert resolved["legacy_request"] == {
        "method": "GET", "path": "/panel.php", "params": {"tab": "things"}, "body": None,
    }


def test_the_table_holding_the_records_is_passed_on_only_when_the_map_names_one(endpoint_map):
    assert "legacy_table" not in resolve(endpoint_map, scenario(q="x"))
    assert resolve(endpoint_map, scenario(source="list"))["legacy_table"] == 2
    edit = scenario("PUT /api/things/{id}", "edit_form", id=7, name="Lamp", fee=550)
    assert resolve(endpoint_map, edit)["legacy_table"] == "none"


def test_path_query_and_body_values_each_go_to_their_place(endpoint_map):
    edit = scenario("PUT /api/things/{id}", "edit_form", id=7, name="Lamp", fee=550)

    resolved = resolve(endpoint_map, edit)

    assert resolved["migrated_request"] == {
        "method": "PUT", "path": "/api/things/7", "params": {},
        "body": {"name": "Lamp", "fee": 550}, "records_at": None,
    }
    assert resolved["legacy_request"] == {
        "method": "POST", "path": "/edit.php", "params": {"thing_id": 7},
        "body": {"save": "1", "txtName": "Lamp", "txtFee": 550},
    }


def test_a_parameter_without_a_value_is_left_out_of_both_requests(endpoint_map):
    edit = scenario("PUT /api/things/{id}", "edit_form", id=7, name="Lamp")

    resolved = resolve(endpoint_map, edit)

    assert resolved["migrated_request"]["body"] == {"name": "Lamp"}
    assert resolved["legacy_request"]["body"] == {"save": "1", "txtName": "Lamp"}


def test_a_value_for_a_parameter_the_endpoint_does_not_have_is_refused(endpoint_map):
    with pytest.raises(ScenarioError, match="s_01: no such parameter: colour"):
        resolve(endpoint_map, scenario(q="x", colour="red"))


@pytest.mark.parametrize(
    ("endpoint", "source"),
    [("GET /api/unknown", "search"), ("GET /api/things", "unknown")],
)
def test_a_scenario_for_an_endpoint_or_page_the_map_lacks_is_refused(endpoint_map, endpoint, source):
    with pytest.raises(ScenarioError, match="the endpoint map has no"):
        resolve(endpoint_map, scenario(endpoint, source))


def test_a_scenario_missing_a_required_part_is_refused(endpoint_map):
    with pytest.raises(ScenarioError, match="does not set: endpoint_id, source_id"):
        resolve(endpoint_map, {"scenario_id": "s_01", "scenario_type": "typical"})


# ------------------------------------------------------------- a whole benchmark


@pytest.fixture
def config(tmp_path: Path):
    (tmp_path / "benchmarks").mkdir()
    (tmp_path / "benchmarks" / "demo.env").write_text(
        "C4_LEGACY_DOCROOT=./app\nC4_LEGACY_SEED=./seed.sql\nC4_LEGACY_DB_NAME=demodb\n"
    )
    config = load_config("demo", environ={}, root=tmp_path)
    config.scenario_dir.mkdir(parents=True)
    config.endpoint_map_file.write_text(json.dumps(MAP))
    return config


def test_every_scenario_file_of_a_benchmark_is_resolved_in_name_order(config):
    (config.scenario_dir / "b_search.json").write_text(json.dumps({**scenario(q="x"), "scenario_id": "b_search"}))
    (config.scenario_dir / "a_list.json").write_text(json.dumps({**scenario(source="list"), "scenario_id": "a_list"}))

    resolved = load_all(config)

    assert [item["scenario_id"] for item in resolved] == ["a_list", "b_search"]
    assert resolved[1]["migrated_request"]["params"] == {"q": "x"}


def test_a_benchmark_with_a_map_and_no_scenarios_has_none(config):
    assert load_all(config) == []


def test_a_benchmark_without_an_endpoint_map_is_reported(config):
    config.endpoint_map_file.unlink()

    with pytest.raises(EndpointMapError, match="No endpoint map"):
        load_all(config)
