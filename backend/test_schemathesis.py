import schemathesis
schema = schemathesis.openapi.from_path('/tmp/schema_final.yml')
schema.base_url = 'http://127.0.0.1:8089'

@schema.parametrize()
def test_api(case):
    response = case.call()
    case.validate_response(response)
