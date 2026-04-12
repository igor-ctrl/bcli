"""Tests for OData query builder."""

from bcapi.odata._query import Query


def test_empty_query():
    q = Query()
    assert q.is_empty
    assert q.to_params() == {}


def test_single_filter():
    q = Query().filter("status eq 'Active'")
    params = q.to_params()
    assert "$filter" in params
    assert "status eq 'Active'" in params["$filter"]


def test_multiple_filters_anded():
    q = Query().filter("status eq 'Active'").filter("name eq 'Test'")
    params = q.to_params()
    assert "(status eq 'Active') and (name eq 'Test')" == params["$filter"]


def test_select():
    q = Query().select("id", "name", "status")
    params = q.to_params()
    assert params["$select"] == "id,name,status"


def test_expand():
    q = Query().expand("items", "dimensions")
    params = q.to_params()
    assert params["$expand"] == "items,dimensions"


def test_orderby():
    q = Query().orderby("name asc")
    assert q.to_params()["$orderby"] == "name asc"


def test_top():
    q = Query().top(10)
    assert q.to_params()["$top"] == "10"


def test_skip():
    q = Query().skip(20)
    assert q.to_params()["$skip"] == "20"


def test_count():
    q = Query().count()
    assert q.to_params()["$count"] == "true"


def test_fluent_chaining():
    q = (
        Query()
        .filter("engineModel eq 'CF34-10E'")
        .select("esn", "status", "nbv")
        .orderby("esn")
        .top(50)
    )
    params = q.to_params()
    assert "$filter" in params
    assert "$select" in params
    assert "$orderby" in params
    assert "$top" in params
    assert not q.is_empty


def test_query_string():
    q = Query().top(5)
    qs = q.to_query_string()
    assert qs.startswith("?")
    assert "$top=5" in qs
