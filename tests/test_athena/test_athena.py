from datetime import datetime

import boto3
import pytest
from botocore.exceptions import ClientError

from moto import mock_aws, settings
from moto.athena.models import QueryResults, athena_backends
from moto.core import DEFAULT_ACCOUNT_ID
from tests import aws_verified


@mock_aws
def test_create_work_group():
    client = boto3.client("athena", region_name="us-east-1")

    client.create_work_group(
        Name="athena_workgroup",
        Description="Test work group",
        Configuration={
            "ResultConfiguration": {
                "OutputLocation": "s3://bucket-name/prefix/",
                "EncryptionConfiguration": {
                    "EncryptionOption": "SSE_KMS",
                    "KmsKey": "aws:arn:kms:1233456789:us-east-1:key/number-1",
                },
            }
        },
    )

    with pytest.raises(ClientError) as exc:
        # The second time should throw an error
        client.create_work_group(
            Name="athena_workgroup",
            Description="duplicate",
            Configuration={
                "ResultConfiguration": {
                    "OutputLocation": "s3://bucket-name/prefix/",
                    "EncryptionConfiguration": {
                        "EncryptionOption": "SSE_KMS",
                        "KmsKey": "aws:arn:kms:1233456789:us-east-1:key/number-1",
                    },
                }
            },
        )
    err = exc.value.response["Error"]
    assert err["Code"] == "InvalidRequestException"
    assert err["Message"] == "WorkGroup already exists"

    # Then test the work group appears in the work group list
    response = client.list_work_groups()

    work_groups = list(
        filter(lambda wg: wg["Name"] != "primary", response["WorkGroups"])
    )
    assert len(work_groups) == 1
    work_group = work_groups[0]
    assert work_group["Name"] == "athena_workgroup"
    assert work_group["Description"] == "Test work group"
    assert work_group["State"] == "ENABLED"


@aws_verified
@pytest.mark.aws_verified
def test_get_primary_workgroup():
    client = boto3.client("athena", region_name="us-east-1")
    assert len(client.list_work_groups()["WorkGroups"]) >= 1

    primary = client.get_work_group(WorkGroup="primary")["WorkGroup"]
    assert primary["Name"] == "primary"
    assert primary["Configuration"] == {
        "ResultConfiguration": {},
        "EnforceWorkGroupConfiguration": False,
        "PublishCloudWatchMetricsEnabled": False,
        "RequesterPaysEnabled": False,
        "EngineVersion": {
            "SelectedEngineVersion": "AUTO",
            "EffectiveEngineVersion": "Athena engine version 3",
        },
        "EnableMinimumEncryptionConfiguration": False,
    }


@aws_verified
@pytest.mark.aws_verified
def test_create_and_get_workgroup():
    client = boto3.client("athena", region_name="us-east-1")

    wg_name = "athena_workgroup"
    create_basic_workgroup(client=client, name=wg_name)

    try:
        work_group = client.get_work_group(WorkGroup=wg_name)["WorkGroup"]
        del work_group["CreationTime"]  # We're not testing creationtime atm
        assert work_group == {
            "Name": wg_name,
            "State": "ENABLED",
            "Configuration": {
                "ResultConfiguration": {"OutputLocation": "s3://bucket-name/prefix/"},
                "EnforceWorkGroupConfiguration": True,
                "PublishCloudWatchMetricsEnabled": False,
                "RequesterPaysEnabled": False,
                "EngineVersion": {
                    "SelectedEngineVersion": "AUTO",
                    "EffectiveEngineVersion": "Athena engine version 3",
                },
                "EnableMinimumEncryptionConfiguration": False,
            },
            "Description": "Test work group",
        }
    finally:
        client.delete_work_group(WorkGroup=wg_name)


@mock_aws
def test_start_query_execution():
    client = boto3.client("athena", region_name="us-east-1")

    create_basic_workgroup(client=client, name="athena_workgroup")
    response = client.start_query_execution(
        QueryString="query1",
        QueryExecutionContext={"Database": "string"},
        ResultConfiguration={"OutputLocation": "string"},
        WorkGroup="athena_workgroup",
    )
    assert "QueryExecutionId" in response

    sec_response = client.start_query_execution(
        QueryString="query2",
        QueryExecutionContext={"Database": "string"},
        ResultConfiguration={"OutputLocation": "string"},
    )
    assert "QueryExecutionId" in sec_response
    assert response["QueryExecutionId"] != sec_response["QueryExecutionId"]


@mock_aws
def test_start_query_execution_without_result_configuration():
    client = boto3.client("athena", region_name="us-east-1")

    create_basic_workgroup(client=client, name="athena_workgroup")
    response = client.start_query_execution(
        QueryString="query1",
        QueryExecutionContext={"Database": "string"},
        WorkGroup="athena_workgroup",
    )
    assert "QueryExecutionId" in response


@mock_aws
def test_start_query_validate_workgroup():
    client = boto3.client("athena", region_name="us-east-1")

    with pytest.raises(ClientError) as err:
        client.start_query_execution(
            QueryString="query1",
            QueryExecutionContext={"Database": "string"},
            ResultConfiguration={"OutputLocation": "string"},
            WorkGroup="unknown_workgroup",
        )
    assert err.value.response["Error"]["Code"] == "InvalidRequestException"
    assert err.value.response["Error"]["Message"] == "WorkGroup does not exist"


@mock_aws
@pytest.mark.parametrize(
    "location", ["s3://bucket-name/prefix/", "s3://bucket-name/prefix_wo_slash"]
)
def test_get_query_execution(location):
    client = boto3.client("athena", region_name="us-east-1")

    query = "SELECT stuff"
    database = "database"
    # Start Query
    exex_id = client.start_query_execution(
        QueryString=query,
        QueryExecutionContext={"Database": database, "Catalog": "awsdatacatalog"},
        ResultConfiguration={"OutputLocation": location},
    )["QueryExecutionId"]
    #
    details = client.get_query_execution(QueryExecutionId=exex_id)["QueryExecution"]
    #
    assert details["QueryExecutionId"] == exex_id
    assert details["Query"] == query
    assert details["StatementType"] == "DML"
    result_config = details["ResultConfiguration"]
    if location.endswith("/"):
        assert result_config["OutputLocation"] == f"{location}{exex_id}.csv"
    else:
        assert result_config["OutputLocation"] == f"{location}/{exex_id}.csv"
    assert (
        details["ResultReuseConfiguration"]["ResultReuseByAgeConfiguration"]["Enabled"]
        is False
    )
    assert details["QueryExecutionContext"]["Database"] == database
    assert details["QueryExecutionContext"]["Catalog"] == "awsdatacatalog"
    assert details["Status"]["State"] == "SUCCEEDED"
    assert isinstance(details["Status"]["SubmissionDateTime"], datetime)
    assert isinstance(details["Status"]["CompletionDateTime"], datetime)
    assert details["Statistics"] == {
        "EngineExecutionTimeInMillis": 0,
        "DataScannedInBytes": 0,
        "TotalExecutionTimeInMillis": 0,
        "QueryQueueTimeInMillis": 0,
        "ServicePreProcessingTimeInMillis": 0,
        "QueryPlanningTimeInMillis": 0,
        "ServiceProcessingTimeInMillis": 0,
        "ResultReuseInformation": {"ReusedPreviousResult": False},
    }
    assert "WorkGroup" not in details


@mock_aws
def test_get_query_execution_with_execution_parameters():
    client = boto3.client("athena", region_name="us-east-1")

    exex_id = client.start_query_execution(
        QueryString="SELECT stuff",
        QueryExecutionContext={"Database": "database"},
        ResultConfiguration={"OutputLocation": "s3://bucket-name/prefix/"},
        ExecutionParameters=["param1", "param2"],
    )["QueryExecutionId"]

    details = client.get_query_execution(QueryExecutionId=exex_id)["QueryExecution"]
    assert details["QueryExecutionId"] == exex_id
    assert details["ExecutionParameters"] == ["param1", "param2"]


@mock_aws
def test_stop_query_execution():
    client = boto3.client("athena", region_name="us-east-1")

    query = "SELECT stuff"
    location = "s3://bucket-name/prefix/"
    database = "database"
    # Start Query
    exex_id = client.start_query_execution(
        QueryString=query,
        QueryExecutionContext={"Database": database},
        ResultConfiguration={"OutputLocation": location},
    )["QueryExecutionId"]
    # Stop Query
    client.stop_query_execution(QueryExecutionId=exex_id)
    # Verify status
    details = client.get_query_execution(QueryExecutionId=exex_id)["QueryExecution"]
    #
    assert details["QueryExecutionId"] == exex_id
    assert details["Status"]["State"] == "CANCELLED"


@mock_aws
def test_start_execution_with_workgroup():
    client = boto3.client("athena", region_name="us-east-1")

    client.create_work_group(
        Name="myworkgroup",
        Description="Test work group",
        Configuration={
            "ResultConfiguration": {"OutputLocation": "s3://bucket-name/prefix/"}
        },
    )

    exec_id = client.start_query_execution(
        QueryString="SELECT stuff",
        QueryExecutionContext={"Database": "database"},
        ResultConfiguration={"OutputLocation": "s3://bucket-name/prefix/"},
        WorkGroup="myworkgroup",
    )["QueryExecutionId"]

    execution = client.get_query_execution(QueryExecutionId=exec_id)["QueryExecution"]
    assert execution["WorkGroup"] == "myworkgroup"


@mock_aws
def test_create_named_query():
    client = boto3.client("athena", region_name="us-east-1")

    # craete named query
    res = client.create_named_query(
        Name="query-name", Database="target_db", QueryString="SELECT * FROM table1"
    )

    assert "NamedQueryId" in res


@mock_aws
def test_get_named_query():
    client = boto3.client("athena", region_name="us-east-1")
    query_name = "query-name"
    database = "target_db"
    query_string = "SELECT * FROM tbl1"
    description = "description of this query"
    # create named query
    res_create = client.create_named_query(
        Name=query_name,
        Database=database,
        QueryString=query_string,
        Description=description,
    )
    query_id = res_create["NamedQueryId"]

    # get named query
    res_get = client.get_named_query(NamedQueryId=query_id)["NamedQuery"]
    assert res_get["Name"] == query_name
    assert res_get["Description"] == description
    assert res_get["Database"] == database
    assert res_get["QueryString"] == query_string
    assert res_get["NamedQueryId"] == query_id


def create_basic_workgroup(client, name):
    client.create_work_group(
        Name=name,
        Description="Test work group",
        Configuration={
            "ResultConfiguration": {"OutputLocation": "s3://bucket-name/prefix/"}
        },
    )


@mock_aws
def test_create_data_catalog():
    client = boto3.client("athena", region_name="us-east-1")
    response = client.create_data_catalog(
        Name="athena_datacatalog",
        Type="GLUE",
        Description="Test data catalog",
        Parameters={"catalog-id": "AWS Test account ID"},
        Tags=[],
    )

    with pytest.raises(ClientError) as exc:
        # The second time should throw an error
        response = client.create_data_catalog(
            Name="athena_datacatalog",
            Type="GLUE",
            Description="Test data catalog",
            Parameters={"catalog-id": "AWS Test account ID"},
            Tags=[],
        )
    err = exc.value.response["Error"]
    assert err["Code"] == "InvalidRequestException"
    assert err["Message"] == "DataCatalog already exists"

    # Then test the work group appears in the work group list
    response = client.list_data_catalogs()

    assert len(response["DataCatalogsSummary"]) == 1
    data_catalog = response["DataCatalogsSummary"][0]
    assert data_catalog["CatalogName"] == "athena_datacatalog"
    assert data_catalog["Type"] == "GLUE"


@mock_aws
def test_create_and_get_data_catalog():
    client = boto3.client("athena", region_name="us-east-1")

    client.create_data_catalog(
        Name="athena_datacatalog",
        Type="GLUE",
        Description="Test data catalog",
        Parameters={"catalog-id": "AWS Test account ID"},
        Tags=[],
    )

    data_catalog = client.get_data_catalog(Name="athena_datacatalog")
    assert data_catalog["DataCatalog"] == {
        "Name": "athena_datacatalog",
        "Description": "Test data catalog",
        "Type": "GLUE",
        "Parameters": {"catalog-id": "AWS Test account ID"},
    }


@mock_aws
def test_get_query_results():
    client = boto3.client("athena", region_name="us-east-1")

    result = client.get_query_results(QueryExecutionId="test")

    assert result["ResultSet"]["Rows"] == []
    assert result["ResultSet"]["ResultSetMetadata"]["ColumnInfo"] == []

    if settings.TEST_DECORATOR_MODE:
        backend = athena_backends[DEFAULT_ACCOUNT_ID]["us-east-1"]
        rows = [{"Data": [{"VarCharValue": ".."}]}]
        column_info = [
            {
                "CatalogName": "string",
                "SchemaName": "string",
                "TableName": "string",
                "Name": "string",
                "Label": "string",
                "Type": "string",
                "Precision": 123,
                "Scale": 123,
                "Nullable": "NOT_NULL",
                "CaseSensitive": True,
            }
        ]
        # This was the documented way to configure query results, before `moto-api/static/athena/query_results` was implemented
        # We should try to keep this for backward compatibility
        results = QueryResults(rows=rows, column_info=column_info)
        backend.query_results["test"] = results

        result = client.get_query_results(QueryExecutionId="test")
        assert result["ResultSet"]["Rows"] == rows
        assert result["ResultSet"]["ResultSetMetadata"]["ColumnInfo"] == column_info


@mock_aws
def test_get_query_results_queue():
    client = boto3.client("athena", region_name="us-east-1")

    result = client.get_query_results(QueryExecutionId="test")

    assert result["ResultSet"]["Rows"] == []
    assert result["ResultSet"]["ResultSetMetadata"]["ColumnInfo"] == []

    if settings.TEST_DECORATOR_MODE:
        backend = athena_backends[DEFAULT_ACCOUNT_ID]["us-east-1"]
        rows = [{"Data": [{"VarCharValue": ".."}]}]
        column_info = [
            {
                "CatalogName": "string",
                "SchemaName": "string",
                "TableName": "string",
                "Name": "string",
                "Label": "string",
                "Type": "string",
                "Precision": 123,
                "Scale": 123,
                "Nullable": "NOT_NULL",
                "CaseSensitive": True,
            }
        ]
        results = QueryResults(rows=rows, column_info=column_info)
        backend.query_results_queue.append(results)

        result = client.get_query_results(
            QueryExecutionId="some-id-not-used-when-results-were-added-to-queue"
        )
        assert result["ResultSet"]["Rows"] == rows
        assert result["ResultSet"]["ResultSetMetadata"]["ColumnInfo"] == column_info

        result = client.get_query_results(QueryExecutionId="other-id")
        assert result["ResultSet"]["Rows"] == []
        assert result["ResultSet"]["ResultSetMetadata"]["ColumnInfo"] == []

        result = client.get_query_results(
            QueryExecutionId="some-id-not-used-when-results-were-added-to-queue"
        )
        assert result["ResultSet"]["Rows"] == rows
        assert result["ResultSet"]["ResultSetMetadata"]["ColumnInfo"] == column_info


@mock_aws
def test_list_query_executions():
    client = boto3.client("athena", region_name="us-east-1")

    create_basic_workgroup(client=client, name="athena_workgroup")
    exec_result = client.start_query_execution(
        QueryString="query1",
        QueryExecutionContext={"Database": "string"},
        ResultConfiguration={"OutputLocation": "string"},
        WorkGroup="athena_workgroup",
    )
    exec_id = exec_result["QueryExecutionId"]

    executions = client.list_query_executions()
    assert len(executions["QueryExecutionIds"]) == 1
    assert executions["QueryExecutionIds"][0] == exec_id


@mock_aws
def test_list_query_executions_by_workgroup():
    client = boto3.client("athena", region_name="us-east-1")

    create_basic_workgroup(client=client, name="athena_workgroup")
    create_basic_workgroup(client=client, name="athena_workgroup_1")

    client.start_query_execution(
        QueryString="query1",
        QueryExecutionContext={"Database": "string"},
        ResultConfiguration={"OutputLocation": "string"},
        WorkGroup="athena_workgroup",
    )
    exec_result = client.start_query_execution(
        QueryString="query1",
        QueryExecutionContext={"Database": "string"},
        ResultConfiguration={"OutputLocation": "string"},
        WorkGroup="athena_workgroup_1",
    )
    exec_id = exec_result["QueryExecutionId"]

    executions = client.list_query_executions(WorkGroup="athena_workgroup_1")
    assert len(executions["QueryExecutionIds"]) == 1
    assert executions["QueryExecutionIds"][0] == exec_id


@mock_aws
def test_list_query_executions_by_workgroup_when_none_match():
    client = boto3.client("athena", region_name="us-east-1")

    create_basic_workgroup(client=client, name="athena_workgroup")
    create_basic_workgroup(client=client, name="athena_workgroup_1")

    client.start_query_execution(
        QueryString="query1",
        QueryExecutionContext={"Database": "string"},
        ResultConfiguration={"OutputLocation": "string"},
        WorkGroup="athena_workgroup",
    )

    executions = client.list_query_executions(WorkGroup="athena_workgroup_1")
    assert len(executions["QueryExecutionIds"]) == 0


@mock_aws
def test_list_named_queries():
    client = boto3.client("athena", region_name="us-east-1")
    create_basic_workgroup(client=client, name="athena_workgroup")
    query_id = client.create_named_query(
        Name="query-name",
        Database="target_db",
        QueryString="SELECT * FROM table1",
        WorkGroup="athena_workgroup",
    )
    list_athena_wg = client.list_named_queries(WorkGroup="athena_workgroup")
    assert list_athena_wg["NamedQueryIds"][0] == query_id["NamedQueryId"]
    list_primary_wg = client.list_named_queries()
    assert len(list_primary_wg["NamedQueryIds"]) == 0


@mock_aws
def test_create_prepared_statement():
    client = boto3.client("athena", region_name="us-east-1")
    create_basic_workgroup(client=client, name="athena_workgroup")
    res = client.create_prepared_statement(
        StatementName="test-statement",
        WorkGroup="athena_workgroup",
        QueryStatement="SELECT * FROM table1",
    )
    metadata = res["ResponseMetadata"]
    assert metadata["HTTPStatusCode"] == 200
    assert metadata["RetryAttempts"] == 0


@mock_aws
def test_get_prepared_statement():
    client = boto3.client("athena", region_name="us-east-1")
    create_basic_workgroup(client=client, name="athena_workgroup")
    client.create_prepared_statement(
        StatementName="stmt-name",
        WorkGroup="athena_workgroup",
        QueryStatement="SELECT * FROM table1",
    )
    resp = client.get_prepared_statement(
        StatementName="stmt-name", WorkGroup="athena_workgroup"
    )
    assert "PreparedStatement" in resp


@mock_aws
def test_get_query_runtime_statistics_no_execution_id():
    client = boto3.client("athena", region_name="us-east-1")
    create_basic_workgroup(client=client, name="athena_workgroup")

    with pytest.raises(ClientError) as exc:
        client.get_query_runtime_statistics(QueryExecutionId="1")

    err = exc.value.response["Error"]
    assert err["Code"] == "InvalidRequestException"
    assert err["Message"] == "QueryExecution 1 was not found"


@mock_aws
def test_get_query_runtime_statistics_with_execution_id():
    client = boto3.client("athena", region_name="us-east-1")
    create_basic_workgroup(client=client, name="athena_workgroup")
    response = client.start_query_execution(
        QueryString="query1",
        QueryExecutionContext={"Database": "string"},
        ResultConfiguration={"OutputLocation": "string"},
        WorkGroup="athena_workgroup",
    )
    assert "QueryExecutionId" in response

    statistics = client.get_query_runtime_statistics(
        QueryExecutionId=response["QueryExecutionId"]
    )

    assert "QueryRuntimeStatistics" in statistics
