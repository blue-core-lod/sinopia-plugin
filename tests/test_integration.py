"""Integration tests for the Sinopia editor using FastAPI TestClient and BeautifulSoup."""
import json
import pytest
from fastapi.testclient import TestClient
from bs4 import BeautifulSoup
from unittest.mock import patch, AsyncMock

# Import the app
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from sinopia_plugin import app


@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    return TestClient(app)


@pytest.fixture
def mock_bluecore_response():
    """Mock response from BLUECORE API."""
    return {
        "@id": "https://dev.bcld.info/works/test-resource-id",
        "@type": ["http://id.loc.gov/ontologies/bibframe/Work"],
        "http://www.w3.org/2000/01/rdf-schema#label": [{"@value": "Test Work"}],
        "http://id.loc.gov/ontologies/bibframe/mainTitle": [{"@value": "Test Title"}],
        "http://id.loc.gov/ontologies/bibframe/title": [
            {
                "@type": ["http://id.loc.gov/ontologies/bibframe/Title"],
                "http://id.loc.gov/ontologies/bibframe/mainTitle": [{"@value": "Test Title"}]
            }
        ],
        "http://id.loc.gov/ontologies/bibframe/language": [
            {"@id": "http://id.loc.gov/vocabulary/languages/eng"}
        ]
    }


def test_editor_page_loads(client):
    """Test that the editor page loads successfully."""
    response = client.get("/sinopia/editor/test-resource-id")
    assert response.status_code == 200
    assert "Sinopia Editor" in response.text


def test_editor_has_triples_section(client):
    """Test that the editor HTML includes the triples section."""
    response = client.get("/sinopia/editor/test-resource-id")
    soup = BeautifulSoup(response.text, "html.parser")

    triples_section = soup.find("div", {"id": "triples-section"})
    assert triples_section is not None


def test_editor_has_main_editor_area(client):
    """Test that the editor HTML includes the main editor area."""
    response = client.get("/sinopia/editor/test-resource-id")
    soup = BeautifulSoup(response.text, "html.parser")

    main_editor = soup.find("div", {"id": "main-editor"})
    assert main_editor is not None


def test_editor_has_left_nav(client):
    """Test that the editor HTML includes the left navigation."""
    response = client.get("/sinopia/editor/test-resource-id")
    soup = BeautifulSoup(response.text, "html.parser")

    left_nav = soup.find("div", {"id": "left-nav"})
    assert left_nav is not None


def test_editor_loads_resource_data(client, mock_bluecore_response):
    """Test that the editor loads and displays resource data."""
    response = client.get("/sinopia/editor/test-resource-id")
    assert response.status_code == 200
    soup = BeautifulSoup(response.text, "html.parser")

    # Check for resource metadata in the HTML
    assert "test-resource-id" in response.text


def test_editor_displays_resource_uri(client):
    """Test that the editor displays the resource URI."""
    response = client.get("/sinopia/editor/test-resource-id")
    soup = BeautifulSoup(response.text, "html.parser")

    uri_element = soup.find("span", {"id": "resource-uri"})
    assert uri_element is not None


def test_editor_has_save_button(client):
    """Test that the editor has a save button."""
    response = client.get("/sinopia/editor/test-resource-id")
    soup = BeautifulSoup(response.text, "html.parser")

    save_button = soup.find("button", string="Save")
    assert save_button is not None


def test_editor_has_close_button(client):
    """Test that the editor has a close button."""
    response = client.get("/sinopia/editor/test-resource-id")
    soup = BeautifulSoup(response.text, "html.parser")

    close_button = soup.find("button", string="Close")
    assert close_button is not None


def test_editor_has_tabs(client):
    """Test that the editor has Navigation/Versions/Relationships tabs."""
    response = client.get("/sinopia/editor/test-resource-id")
    soup = BeautifulSoup(response.text, "html.parser")

    nav_tab = soup.find("a", {"id": "tab-nav-btn"})
    ver_tab = soup.find("a", {"id": "tab-ver-btn"})
    rel_tab = soup.find("a", {"id": "tab-rel-btn"})

    assert nav_tab is not None
    assert ver_tab is not None
    assert rel_tab is not None


def test_editor_has_copy_uri_button(client):
    """Test that the editor has a Copy URI button."""
    response = client.get("/sinopia/editor/test-resource-id")
    soup = BeautifulSoup(response.text, "html.parser")

    copy_btn = soup.find("button", {"id": "copy-uri-btn"})
    assert copy_btn is not None
