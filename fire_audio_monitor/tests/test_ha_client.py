import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.ha_client import HomeAssistantClient


@pytest.mark.parametrize("status,json_error", [(200, False), (404, False), (500, False), (200, True)])
def test_get_state_closes_response(status, json_error):
    client = HomeAssistantClient(token="secret")
    response = Mock(status_code=status)
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    if status >= 400 and status != 404:
        response.raise_for_status.side_effect = requests.HTTPError("bad")
    if json_error:
        response.json.side_effect = ValueError("bad json")
    else:
        response.json.return_value = {"state": "on"}
    client.session.get = Mock(return_value=response)
    try:
        client.get_state("binary_sensor.test")
    except (requests.HTTPError, ValueError):
        pass
    response.__exit__.assert_called_once()


def test_timeout_and_session_close():
    client = HomeAssistantClient(token="secret")
    client.session.get = Mock(side_effect=requests.Timeout("slow"))
    with pytest.raises(requests.Timeout):
        client.get_state("binary_sensor.test")
    client.session.close = Mock()
    client.close()
    client.close()
    client.session.close.assert_called_once()
    with pytest.raises(RuntimeError, match="session is closed"):
        client.get_state("binary_sensor.test")


def test_fire_event_consumes_and_closes_response():
    client = HomeAssistantClient(token="secret")
    response = Mock(content=b"{}")
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    client.session.post = Mock(return_value=response)
    client.fire_event("test_event", {"value": 1})
    _ = response.content
    response.__exit__.assert_called_once()
