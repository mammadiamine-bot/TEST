import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import Settings
from app.db import Db
from app.service import Core

TOKEN = "test-token"


class Clock:
    t = 1_800_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def online():
    return {"v": True}


@pytest.fixture
def core(tmp_path, clock, online):
    s = Settings(data_dir=tmp_path, api_token=TOKEN)
    return Core(s, Db(":memory:"), cloud_probe=lambda: online["v"], now=clock)


@pytest.fixture
def client(core):
    c = TestClient(create_app(core.s, core), headers={"Authorization": f"Bearer {TOKEN}"})
    return c


def onboard(client, scenario="broken", scope="repair", minutes=30):
    d = client.post("/devices", json={"name": "Lab PC", "address": "mock", "transport": "mock", "owner_name": "Sara",
                                      "owner_contact": "sara@example.com", "authorization_note": "Owner signed form #12", "scenario": scenario}).json()
    d = client.post(f"/devices/{d['id']}/pair", json={"code": d["pairing_code"]}).json()
    s = client.post(f"/devices/{d['id']}/sessions", json={"scope": scope, "minutes": minutes}).json()
    client.post(f"/sessions/{s['id']}/consent", json={"code": s["consent_code"], "person": "Sara"})
    return d["id"], s["id"]
