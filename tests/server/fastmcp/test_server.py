import base64
import json
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from pydantic import AnyUrl
from starlette.routing import Mount, Route

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.prompts.base import Message, UserMessage
from mcp.server.fastmcp.resources import FileResource, FunctionResource
from mcp.server.fastmcp.utilities.types import Image
from mcp.shared.exceptions import McpError
from mcp.shared.memory import (
    create_connected_server_and_client_session as client_session,
)
from mcp.types import (
    AudioContent,
    BlobResourceContents,
    ContentBlock,
    EmbeddedResource,
    ImageContent,
    TextContent,
    TextResourceContents,
)

if TYPE_CHECKING:
    from mcp.server.fastmcp import Context


class TestServer:
    @pytest.mark.anyio
    async def test_create_server(self):
        mcp = FastMCP(instructions="Server instructions")
        assert mcp.name == "FastMCP"
        assert mcp.instructions == "Server instructions"

    @pytest.mark.anyio
    async def test_normalize_path(self):
        """Test path normalization for mount paths."""
        mcp = FastMCP()

        # Test root path
        assert mcp._normalize_path("/", "/messages/") == "/messages/"

        # Test path with trailing slash
        assert mcp._normalize_path("/github/", "/messages/") == "/github/messages/"

        # Test path without trailing slash
        assert mcp._normalize_path("/github", "/messages/") == "/github/messages/"

        # Test endpoint without leading slash
        assert mcp._normalize_path("/github", "messages/") == "/github/messages/"

        # Test both with trailing/leading slashes
        assert mcp._normalize_path("/api/", "/v1/") == "/api/v1/"

    @pytest.mark.anyio
    async def test_sse_app_with_mount_path(self):
        """Test SSE app creation with different mount paths."""
        # Test with default mount path
        mcp = FastMCP()
        with patch.object(mcp, "_normalize_path", return_value="/messages/") as mock_normalize:
            mcp.sse_app()
            # Verify _normalize_path was called with correct args
            mock_normalize.assert_called_once_with("/", "/messages/")

        # Test with custom mount path in settings
        mcp = FastMCP()
        mcp.settings.mount_path = "/custom"
        with patch.object(mcp, "_normalize_path", return_value="/custom/messages/") as mock_normalize:
            mcp.sse_app()
            # Verify _normalize_path was called with correct args
            mock_normalize.assert_called_once_with("/custom", "/messages/")

        # Test with mount_path parameter
        mcp = FastMCP()
        with patch.object(mcp, "_normalize_path", return_value="/param/messages/") as mock_normalize:
            mcp.sse_app(mount_path="/param")
            # Verify _normalize_path was called with correct args
            mock_normalize.assert_called_once_with("/param", "/messages/")

    @pytest.mark.anyio
    async def test_starlette_routes_with_mount_path(self):
        """Test that Starlette routes are correctly configured with mount path."""
        # Test with mount path in settings
        mcp = FastMCP()
        mcp.settings.mount_path = "/api"
        app = mcp.sse_app()

        # Find routes by type
        sse_routes = [r for r in app.routes if isinstance(r, Route)]
        mount_routes = [r for r in app.routes if isinstance(r, Mount)]

        # Verify routes exist
        assert len(sse_routes) == 1, "Should have one SSE route"
        assert len(mount_routes) == 1, "Should have one mount route"

        # Verify path values
        assert sse_routes[0].path == "/sse", "SSE route path should be /sse"
        assert mount_routes[0].path == "/messages", "Mount route path should be /messages"

        # Test with mount path as parameter
        mcp = FastMCP()
        app = mcp.sse_app(mount_path="/param")

        # Find routes by type
        sse_routes = [r for r in app.routes if isinstance(r, Route)]
        mount_routes = [r for r in app.routes if isinstance(r, Mount)]

        # Verify routes exist
        assert len(sse_routes) == 1, "Should have one SSE route"
        assert len(mount_routes) == 1, "Should have one mount route"

        # Verify path values
        assert sse_routes[0].path == "/sse", "SSE route path should be /sse"
        assert mount_routes[0].path == "/messages", "Mount route path should be /messages"

    @pytest.mark.anyio
    async def test_non_ascii_description(self):
        """Test that FastMCP handles non-ASCII characters in descriptions correctly"""
        mcp = FastMCP()

        @mcp.tool(description=("🌟 This tool uses emojis and UTF-8 characters: á é í ó ú ñ 漢字 🎉"))
        def hello_world(name: str = "世界") -> str:
            return f"¡Hola, {name}! 👋"

        async with client_session(mcp._mcp_server) as client:
            tools = await client.list_tools()
            assert len(tools.tools) == 1
            tool = tools.tools[0]
            assert tool.description is not None
            assert "🌟" in tool.description
            assert "漢字" in tool.description
            assert "🎉" in tool.description

            result = await client.call_tool("hello_world", {})
            assert len(result.content) == 1
            content = result.content[0]
            assert isinstance(content, TextContent)
            assert "¡Hola, 世界! 👋" == content.text

    @pytest.mark.anyio
    async def test_add_tool_decorator(self):
        mcp = FastMCP()

        @mcp.tool()
        def add(x: int, y: int) -> int:
            return x + y

        assert len(mcp._tool_manager.list_tools()) == 1

    @pytest.mark.anyio
    async def test_add_tool_decorator_incorrect_usage(self):
        mcp = FastMCP()

        with pytest.raises(TypeError, match="The @tool decorator was used incorrectly"):

            @mcp.tool  # Missing parentheses #type: ignore
            def add(x: int, y: int) -> int:
                return x + y

    @pytest.mark.anyio
    async def test_add_resource_decorator(self):
        mcp = FastMCP()

        @mcp.resource("r://{x}")
        def get_data(x: str) -> str:
            return f"Data: {x}"

        assert len(mcp._resource_manager._templates) == 1

    @pytest.mark.anyio
    async def test_add_resource_decorator_incorrect_usage(self):
        mcp = FastMCP()

        with pytest.raises(TypeError, match="The @resource decorator was used incorrectly"):

            @mcp.resource  # Missing parentheses #type: ignore
            def get_data(x: str) -> str:
                return f"Data: {x}"


def tool_fn(x: int, y: int) -> int:
    return x + y


def error_tool_fn() -> None:
    raise ValueError("Test error")


def image_tool_fn(path: str) -> Image:
    return Image(path)


def mixed_content_tool_fn() -> list[ContentBlock]:
    return [
        TextContent(type="text", text="Hello"),
        ImageContent(type="image", data="abc", mimeType="image/png"),
        AudioContent(type="audio", data="def", mimeType="audio/wav"),
    ]


class TestServerTools:
    @pytest.mark.anyio
    async def test_add_tool(self):
        mcp = FastMCP()
        mcp.add_tool(tool_fn)
        mcp.add_tool(tool_fn)
        assert len(mcp._tool_manager.list_tools()) == 1

    @pytest.mark.anyio
    async def test_list_tools(self):
        mcp = FastMCP()
        mcp.add_tool(tool_fn)
        async with client_session(mcp._mcp_server) as client:
            tools = await client.list_tools()
            assert len(tools.tools) == 1

    @pytest.mark.anyio
    async def test_call_tool(self):
        mcp = FastMCP()
        mcp.add_tool(tool_fn)
        async with client_session(mcp._mcp_server) as client:
            result = await client.call_tool("my_tool", {"arg1": "value"})
            assert not hasattr(result, "error")
            assert len(result.content) > 0

    @pytest.mark.anyio
    async def test_tool_exception_handling(self):
        mcp = FastMCP()
        mcp.add_tool(error_tool_fn)
        async with client_session(mcp._mcp_server) as client:
            result = await client.call_tool("error_tool_fn", {})
            assert len(result.content) == 1
            content = result.content[0]
            assert isinstance(content, TextContent)
            assert "Test error" in content.text
            assert result.isError is True

    @pytest.mark.anyio
    async def test_tool_error_handling(self):
        mcp = FastMCP()
        mcp.add_tool(error_tool_fn)
        async with client_session(mcp._mcp_server) as client:
            result = await client.call_tool("error_tool_fn", {})
            assert len(result.content) == 1
            content = result.content[0]
            assert isinstance(content, TextContent)
            assert "Test error" in content.text
            assert result.isError is True

    @pytest.mark.anyio
    async def test_tool_error_details(self):
        """Test that exception details are properly formatted in the response"""
        mcp = FastMCP()
        mcp.add_tool(error_tool_fn)
        async with client_session(mcp._mcp_server) as client:
            result = await client.call_tool("error_tool_fn", {})
            content = result.content[0]
            assert isinstance(content, TextContent)
            assert isinstance(content.text, str)
            assert "Test error" in content.text
            assert result.isError is True

    @pytest.mark.anyio
    async def test_tool_return_value_conversion(self):
        mcp = FastMCP()
        mcp.add_tool(tool_fn)
        async with client_session(mcp._mcp_server) as client:
            result = await client.call_tool("tool_fn", {"x": 1, "y": 2})
            assert len(result.content) == 1
            content = result.content[0]
            assert isinstance(content, TextContent)
            assert content.text == "3"

    @pytest.mark.anyio
    async def test_tool_image_helper(self, tmp_path: Path):
        # Create a test image
        image_path = tmp_path / "test.png"
        image_path.write_bytes(b"fake png data")

        mcp = FastMCP()
        mcp.add_tool(image_tool_fn)
        async with client_session(mcp._mcp_server) as client:
            result = await client.call_tool("image_tool_fn", {"path": str(image_path)})
            assert len(result.content) == 1
            content = result.content[0]
            assert isinstance(content, ImageContent)
            assert content.type == "image"
            assert content.mimeType == "image/png"
            # Verify base64 encoding
            decoded = base64.b64decode(content.data)
            assert decoded == b"fake png data"

    @pytest.mark.anyio
    async def test_tool_mixed_content(self):
        mcp = FastMCP()
        mcp.add_tool(mixed_content_tool_fn)
        async with client_session(mcp._mcp_server) as client:
            result = await client.call_tool("mixed_content_tool_fn", {})
            assert len(result.content) == 3
            content1, content2, content3 = result.content
            assert isinstance(content1, TextContent)
            assert content1.text == "Hello"
            assert isinstance(content2, ImageContent)
            assert content2.mimeType == "image/png"
            assert content2.data == "abc"
            assert isinstance(content3, AudioContent)
            assert content3.mimeType == "audio/wav"
            assert content3.data == "def"

    @pytest.mark.anyio
    async def test_tool_mixed_list_with_image(self, tmp_path: Path):
        """Test that lists containing Image objects and other types are handled
        correctly"""
        # Create a test image
        image_path = tmp_path / "test.png"
        image_path.write_bytes(b"test image data")

        def mixed_list_fn() -> list:
            return [
                "text message",
                Image(image_path),
                {"key": "value"},
                TextContent(type="text", text="direct content"),
            ]

        mcp = FastMCP()
        mcp.add_tool(mixed_list_fn)
        async with client_session(mcp._mcp_server) as client:
            result = await client.call_tool("mixed_list_fn", {})
            assert len(result.content) == 4
            # Check text conversion
            content1 = result.content[0]
            assert isinstance(content1, TextContent)
            assert content1.text == "text message"
            # Check image conversion
            content2 = result.content[1]
            assert isinstance(content2, ImageContent)
            assert content2.mimeType == "image/png"
            assert base64.b64decode(content2.data) == b"test image data"
            # Check dict conversion
            content3 = result.content[2]
            assert isinstance(content3, TextContent)
            assert '"key": "value"' in content3.text
            # Check direct TextContent
            content4 = result.content[3]
            assert isinstance(content4, TextContent)
            assert content4.text == "direct content"


class TestServerResources:
    @pytest.mark.anyio
    async def test_text_resource(self):
        mcp = FastMCP()

        def get_text():
            return "Hello, world!"

        resource = FunctionResource(uri=AnyUrl("resource://test"), name="test", fn=get_text)
        mcp.add_resource(resource)

        async with client_session(mcp._mcp_server) as client:
            result = await client.read_resource(AnyUrl("resource://test"))
            assert isinstance(result.contents[0], TextResourceContents)
            assert result.contents[0].text == "Hello, world!"

    @pytest.mark.anyio
    async def test_binary_resource(self):
        mcp = FastMCP()

        def get_binary():
            return b"Binary data"

        resource = FunctionResource(
            uri=AnyUrl("resource://binary"),
            name="binary",
            fn=get_binary,
            mime_type="application/octet-stream",
        )
        mcp.add_resource(resource)

        async with client_session(mcp._mcp_server) as client:
            result = await client.read_resource(AnyUrl("resource://binary"))
            assert isinstance(result.contents[0], BlobResourceContents)
            assert result.contents[0].blob == base64.b64encode(b"Binary data").decode()

    @pytest.mark.anyio
    async def test_file_resource_text(self, tmp_path: Path):
        mcp = FastMCP()

        # Create a text file
        text_file = tmp_path / "test.txt"
        text_file.write_text("Hello from file!")

        resource = FileResource(uri=AnyUrl("file://test.txt"), name="test.txt", path=text_file)
        mcp.add_resource(resource)

        async with client_session(mcp._mcp_server) as client:
            result = await client.read_resource(AnyUrl("file://test.txt"))
            assert isinstance(result.contents[0], TextResourceContents)
            assert result.contents[0].text == "Hello from file!"

    @pytest.mark.anyio
    async def test_file_resource_binary(self, tmp_path: Path):
        mcp = FastMCP()

        # Create a binary file
        binary_file = tmp_path / "test.bin"
        binary_file.write_bytes(b"Binary file data")

        resource = FileResource(
            uri=AnyUrl("file://test.bin"),
            name="test.bin",
            path=binary_file,
            mime_type="application/octet-stream",
        )
        mcp.add_resource(resource)

        async with client_session(mcp._mcp_server) as client:
            result = await client.read_resource(AnyUrl("file://test.bin"))
            assert isinstance(result.contents[0], BlobResourceContents)
            assert result.contents[0].blob == base64.b64encode(b"Binary file data").decode()

    @pytest.mark.anyio
    async def test_resource_with_form_style_query(self):
        """Test that resources with form-style query expansion work correctly"""
        mcp = FastMCP()

        @mcp.resource("resource://{category}/{id}{?filter,sort,limit}")
        def get_item(
            category: str,
            id: str,
            filter: str = "all",
            sort: str = "name",
            limit: int = 10,
        ) -> str:
            return f"Item {id} in {category}, filtered by {filter}, sorted by {sort}, " f"limited to {limit}"

        async with client_session(mcp._mcp_server) as client:
            # Test with default values
            result = await client.read_resource(AnyUrl("resource://electronics/1234"))
            assert isinstance(result.contents[0], TextResourceContents)
            assert (
                result.contents[0].text == "Item 1234 in electronics, filtered by all, sorted by name, " "limited to 10"
            )

            # Test with query parameters
            result = await client.read_resource(AnyUrl("resource://electronics/1234?filter=new&sort=price&limit=20"))
            assert isinstance(result.contents[0], TextResourceContents)
            assert (
                result.contents[0].text == "Item 1234 in electronics, filtered by new, sorted by price, "
                "limited to 20"
            )

            # Test with partial query parameters
            result = await client.read_resource(AnyUrl("resource://electronics/1234?filter=used"))
            assert isinstance(result.contents[0], TextResourceContents)
            assert (
                result.contents[0].text == "Item 1234 in electronics, filtered by used, sorted by name, "
                "limited to 10"
            )

    @pytest.mark.anyio
    async def test_function_resource(self):
        mcp = FastMCP()

        @mcp.resource("function://test", name="test_get_data")
        def get_data() -> str:
            """get_data returns a string"""
            return "Hello, world!"

        async with client_session(mcp._mcp_server) as client:
            resources = await client.list_resources()
            assert len(resources.resources) == 1
            resource = resources.resources[0]
            assert resource.description == "get_data returns a string"
            assert resource.uri == AnyUrl("function://test")
            assert resource.name == "test_get_data"
            assert resource.mimeType == "text/plain"


class TestServerResourceTemplates:
    @pytest.mark.anyio
    async def test_resource_with_params(self):
        """Test that a resource with function parameters raises an error if the URI
        parameters don't match"""
        mcp = FastMCP()

        with pytest.raises(
            ValueError,
            match="Mismatch between URI path parameters .* and " "required function parameters .*",
        ):

            @mcp.resource("resource://data")
            def get_data_fn(param: str) -> str:
                return f"Data: {param}"

    @pytest.mark.anyio
    async def test_resource_with_uri_params(self):
        """Test that a resource with URI parameters is automatically a template"""
        mcp = FastMCP()

        with pytest.raises(
            ValueError,
            match="Mismatch between URI path parameters .* and " "required function parameters .*",
        ):

            @mcp.resource("resource://{param}")
            def get_data() -> str:
                return "Data"

    @pytest.mark.anyio
    async def test_resource_with_untyped_params(self):
        """Test that a resource with untyped parameters raises an error"""
        mcp = FastMCP()

        @mcp.resource("resource://{param}")
        def get_data(param) -> str:
            return "Data"

    @pytest.mark.anyio
    async def test_resource_matching_params(self):
        """Test that a resource with matching URI and function parameters works"""
        mcp = FastMCP()

        @mcp.resource("resource://{name}/data")
        def get_data(name: str) -> str:
            return f"Data for {name}"

        async with client_session(mcp._mcp_server) as client:
            result = await client.read_resource(AnyUrl("resource://test/data"))
            assert isinstance(result.contents[0], TextResourceContents)
            assert result.contents[0].text == "Data for test"

    @pytest.mark.anyio
    async def test_resource_with_optional_params(self):
        """Test that resources with optional parameters work correctly"""
        mcp = FastMCP()

        @mcp.resource("resource://{name}/data")
        def get_data_with_options(name: str, format: str = "text", limit: int = 10) -> str:
            return f"Data for {name} in {format} format with limit {limit}"

        async with client_session(mcp._mcp_server) as client:
            # Test with default values
            result = await client.read_resource(AnyUrl("resource://test/data"))
            assert isinstance(result.contents[0], TextResourceContents)
            assert result.contents[0].text == "Data for test in text format with limit 10"

            # Test with query parameters
            result = await client.read_resource(AnyUrl("resource://test/data?format=json&limit=20"))
            assert isinstance(result.contents[0], TextResourceContents)
            assert result.contents[0].text == "Data for test in json format with limit 20"

            # Test with partial query parameters
            result = await client.read_resource(AnyUrl("resource://test/data?format=xml"))
            assert isinstance(result.contents[0], TextResourceContents)
            assert result.contents[0].text == "Data for test in xml format with limit 10"

    @pytest.mark.anyio
    async def test_resource_mismatched_params(self):
        """Test that mismatched parameters raise an error"""
        mcp = FastMCP()

        with pytest.raises(
            ValueError,
            match="Mismatch between URI path parameters .* and " "required function parameters .*",
        ):

            @mcp.resource("resource://{name}/data")
            def get_data(user: str) -> str:
                return f"Data for {user}"

    @pytest.mark.anyio
    async def test_resource_multiple_params(self):
        """Test that multiple parameters work correctly"""
        mcp = FastMCP()

        @mcp.resource("resource://{org}/{repo}/data")
        def get_data(org: str, repo: str) -> str:
            return f"Data for {org}/{repo}"

        async with client_session(mcp._mcp_server) as client:
            result = await client.read_resource(AnyUrl("resource://cursor/fastmcp/data"))
            assert isinstance(result.contents[0], TextResourceContents)
            assert result.contents[0].text == "Data for cursor/fastmcp"

    @pytest.mark.anyio
    async def test_resource_multiple_mismatched_params(self):
        """Test that mismatched parameters raise an error"""
        mcp = FastMCP()

        with pytest.raises(
            ValueError,
            match="Mismatch between URI path parameters .* and " "required function parameters .*",
        ):

            @mcp.resource("resource://{org}/{repo}/data")
            def get_data_mismatched(org: str, repo_2: str) -> str:
                return f"Data for {org}"

        """Test that a resource with no parameters works as a regular resource"""
        mcp = FastMCP()

        @mcp.resource("resource://static")
        def get_static_data() -> str:
            return "Static data"

        async with client_session(mcp._mcp_server) as client:
            result = await client.read_resource(AnyUrl("resource://static"))
            assert isinstance(result.contents[0], TextResourceContents)
            assert result.contents[0].text == "Static data"

    @pytest.mark.anyio
    async def test_template_to_resource_conversion(self):
        """Test that templates are properly converted to resources when accessed"""
        mcp = FastMCP()

        @mcp.resource("resource://{name}/data")
        def get_data(name: str) -> str:
            return f"Data for {name}"

        # Should be registered as a template
        assert len(mcp._resource_manager._templates) == 1
        assert len(await mcp.list_resources()) == 0

        # When accessed, should create a concrete resource
        resource = await mcp._resource_manager.get_resource("resource://test/data")
        assert isinstance(resource, FunctionResource)
        result = await resource.read()
        assert result == "Data for test"

    @pytest.mark.anyio
    async def test_resource_optional_param_validation_fallback_and_url_encoding(
        self,
    ):
        """Test handling of optional param validation fallback & URL encoding."""
        mcp = FastMCP()

        @mcp.resource("resource://test_item/{item_id}{?name,count,active}")
        def get_test_item_details(
            item_id: str,
            name: str = "default_name",
            count: int = 0,
            active: bool = False,
        ) -> dict:
            return {
                "item_id": item_id,
                "name": name,
                "count": count,
                "active": active,
            }

        async with client_session(mcp._mcp_server) as client:
            # 1. All defaults
            res1_uri = "resource://test_item/item001"
            res1_content_result = await client.read_resource(AnyUrl(res1_uri))
            assert res1_content_result.contents and isinstance(res1_content_result.contents[0], TextResourceContents)
            data1 = json.loads(res1_content_result.contents[0].text)
            assert data1 == {
                "item_id": "item001",
                "name": "default_name",
                "count": 0,
                "active": False,
            }

            # 2. Valid optional params (name is URL encoded)
            res2_uri = "resource://test_item/item002?name=My%20Product&count=10&active=true"
            res2_content_result = await client.read_resource(AnyUrl(res2_uri))
            assert res2_content_result.contents and isinstance(res2_content_result.contents[0], TextResourceContents)
            data2 = json.loads(res2_content_result.contents[0].text)
            assert data2 == {
                "item_id": "item002",
                "name": "My Product",  # Decoded
                "count": 10,
                "active": True,
            }

            # 3. Invalid 'count' (optional int), valid 'name', 'active' not provided
            # count=notanint should make it use default_count = 0
            res3_uri = "resource://test_item/item003?name=Another%20Item&count=notanint"
            res3_content_result = await client.read_resource(AnyUrl(res3_uri))
            assert res3_content_result.contents and isinstance(res3_content_result.contents[0], TextResourceContents)
            data3 = json.loads(res3_content_result.contents[0].text)
            assert data3 == {
                "item_id": "item003",
                "name": "Another Item",
                "count": 0,  # Fallback to default
                "active": False,  # Fallback to default
            }

            # 4. Invalid 'active' (optional bool), valid 'count', 'name' not provided
            # active=notabool should make it use default_active = False
            res4_uri = "resource://test_item/item004?count=50&active=notabool"
            res4_content_result = await client.read_resource(AnyUrl(res4_uri))
            assert res4_content_result.contents and isinstance(res4_content_result.contents[0], TextResourceContents)
            data4 = json.loads(res4_content_result.contents[0].text)
            assert data4 == {
                "item_id": "item004",
                "name": "default_name",  # Fallback to default
                "count": 50,
                "active": False,  # Fallback to default
            }

            # 5. Empty value for optional 'name' (string type)
            # name= (empty value) should fall back to default
            res5_uri = "resource://test_item/item005?name="
            res5_content_result = await client.read_resource(AnyUrl(res5_uri))
            assert res5_content_result.contents and isinstance(res5_content_result.contents[0], TextResourceContents)
            data5 = json.loads(res5_content_result.contents[0].text)
            assert data5 == {
                "item_id": "item005",
                "name": "default_name",  # Fallback to default
                "count": 0,
                "active": False,
            }

            # 6. Empty value for optional 'count' (int type)
            # count= (empty value) should fall back to default
            res6_uri = "resource://test_item/item006?count="
            res6_content_result = await client.read_resource(AnyUrl(res6_uri))
            assert res6_content_result.contents and isinstance(res6_content_result.contents[0], TextResourceContents)
            data6 = json.loads(res6_content_result.contents[0].text)
            assert data6 == {
                "item_id": "item006",
                "name": "default_name",
                "count": 0,  # Fallback to default because param is removed by parse_qs
                "active": False,
            }

        # Test required param failing validation at server level
        @mcp.resource("resource://req_fail/{req_id}/details")
        def get_req_details(req_id: int, detail_type: str = "summary") -> dict:
            return {"req_id": req_id, "detail_type": detail_type}

        async with client_session(mcp._mcp_server) as client:
            invalid_req_uri = "resource://req_fail/notanint/details"
            # The FastMCP.read_resource wraps internal errors,
            # from template.create_resource, into a ResourceError, as McpError.
            with pytest.raises(McpError, match="Error creating resource from template"):
                await client.read_resource(AnyUrl(invalid_req_uri))


class TestContextInjection:
    """Test context injection in tools."""

    @pytest.mark.anyio
    async def test_context_detection(self):
        """Test that context parameters are properly detected."""
        mcp = FastMCP()

        def tool_with_context(x: int, ctx: Context) -> str:
            return f"Request {ctx.request_id}: {x}"

        tool = mcp._tool_manager.add_tool(tool_with_context)
        assert tool.context_kwarg == "ctx"

    @pytest.mark.anyio
    async def test_context_injection(self):
        """Test that context is properly injected into tool calls."""
        mcp = FastMCP()

        def tool_with_context(x: int, ctx: Context) -> str:
            assert ctx.request_id is not None
            return f"Request {ctx.request_id}: {x}"

        mcp.add_tool(tool_with_context)
        async with client_session(mcp._mcp_server) as client:
            result = await client.call_tool("tool_with_context", {"x": 42})
            assert len(result.content) == 1
            content = result.content[0]
            assert isinstance(content, TextContent)
            assert "Request" in content.text
            assert "42" in content.text

    @pytest.mark.anyio
    async def test_async_context(self):
        """Test that context works in async functions."""
        mcp = FastMCP()

        async def async_tool(x: int, ctx: Context) -> str:
            assert ctx.request_id is not None
            return f"Async request {ctx.request_id}: {x}"

        mcp.add_tool(async_tool)
        async with client_session(mcp._mcp_server) as client:
            result = await client.call_tool("async_tool", {"x": 42})
            assert len(result.content) == 1
            content = result.content[0]
            assert isinstance(content, TextContent)
            assert "Async request" in content.text
            assert "42" in content.text

    @pytest.mark.anyio
    async def test_context_logging(self):
        import mcp.server.session

        """Test that context logging methods work."""
        mcp = FastMCP()

        async def logging_tool(msg: str, ctx: Context) -> str:
            await ctx.debug("Debug message")
            await ctx.info("Info message")
            await ctx.warning("Warning message")
            await ctx.error("Error message")
            return f"Logged messages for {msg}"

        mcp.add_tool(logging_tool)

        with patch("mcp.server.session.ServerSession.send_log_message") as mock_log:
            async with client_session(mcp._mcp_server) as client:
                result = await client.call_tool("logging_tool", {"msg": "test"})
                assert len(result.content) == 1
                content = result.content[0]
                assert isinstance(content, TextContent)
                assert "Logged messages for test" in content.text

                assert mock_log.call_count == 4
                mock_log.assert_any_call(
                    level="debug",
                    data="Debug message",
                    logger=None,
                    related_request_id="1",
                )
                mock_log.assert_any_call(
                    level="info",
                    data="Info message",
                    logger=None,
                    related_request_id="1",
                )
                mock_log.assert_any_call(
                    level="warning",
                    data="Warning message",
                    logger=None,
                    related_request_id="1",
                )
                mock_log.assert_any_call(
                    level="error",
                    data="Error message",
                    logger=None,
                    related_request_id="1",
                )

    @pytest.mark.anyio
    async def test_optional_context(self):
        """Test that context is optional."""
        mcp = FastMCP()

        def no_context(x: int) -> int:
            return x * 2

        mcp.add_tool(no_context)
        async with client_session(mcp._mcp_server) as client:
            result = await client.call_tool("no_context", {"x": 21})
            assert len(result.content) == 1
            content = result.content[0]
            assert isinstance(content, TextContent)
            assert content.text == "42"

    @pytest.mark.anyio
    async def test_context_resource_access(self):
        """Test that context can access resources."""
        mcp = FastMCP()

        @mcp.resource("test://data")
        def test_resource() -> str:
            return "resource data"

        @mcp.tool()
        async def tool_with_resource(ctx: Context) -> str:
            r_iter = await ctx.read_resource("test://data")
            r_list = list(r_iter)
            assert len(r_list) == 1
            r = r_list[0]
            return f"Read resource: {r.content} with mime type {r.mime_type}"

        async with client_session(mcp._mcp_server) as client:
            result = await client.call_tool("tool_with_resource", {})
            assert len(result.content) == 1
            content = result.content[0]
            assert isinstance(content, TextContent)
            assert "Read resource: resource data" in content.text


class TestServerPrompts:
    """Test prompt functionality in FastMCP server."""

    @pytest.mark.anyio
    async def test_prompt_decorator(self):
        """Test that the prompt decorator registers prompts correctly."""
        mcp = FastMCP()

        @mcp.prompt()
        def fn() -> str:
            return "Hello, world!"

        prompts = mcp._prompt_manager.list_prompts()
        assert len(prompts) == 1
        assert prompts[0].name == "fn"
        # Don't compare functions directly since validate_call wraps them
        content = await prompts[0].render()
        assert isinstance(content[0].content, TextContent)
        assert content[0].content.text == "Hello, world!"

    @pytest.mark.anyio
    async def test_prompt_decorator_with_name(self):
        """Test prompt decorator with custom name."""
        mcp = FastMCP()

        @mcp.prompt(name="custom_name")
        def fn() -> str:
            return "Hello, world!"

        prompts = mcp._prompt_manager.list_prompts()
        assert len(prompts) == 1
        assert prompts[0].name == "custom_name"
        content = await prompts[0].render()
        assert isinstance(content[0].content, TextContent)
        assert content[0].content.text == "Hello, world!"

    @pytest.mark.anyio
    async def test_prompt_decorator_with_description(self):
        """Test prompt decorator with custom description."""
        mcp = FastMCP()

        @mcp.prompt(description="A custom description")
        def fn() -> str:
            return "Hello, world!"

        prompts = mcp._prompt_manager.list_prompts()
        assert len(prompts) == 1
        assert prompts[0].description == "A custom description"
        content = await prompts[0].render()
        assert isinstance(content[0].content, TextContent)
        assert content[0].content.text == "Hello, world!"

    def test_prompt_decorator_error(self):
        """Test error when decorator is used incorrectly."""
        mcp = FastMCP()
        with pytest.raises(TypeError, match="decorator was used incorrectly"):

            @mcp.prompt  # type: ignore
            def fn() -> str:
                return "Hello, world!"

    @pytest.mark.anyio
    async def test_list_prompts(self):
        """Test listing prompts through MCP protocol."""
        mcp = FastMCP()

        @mcp.prompt()
        def fn(name: str, optional: str = "default") -> str:
            return f"Hello, {name}!"

        async with client_session(mcp._mcp_server) as client:
            result = await client.list_prompts()
            assert result.prompts is not None
            assert len(result.prompts) == 1
            prompt = result.prompts[0]
            assert prompt.name == "fn"
            assert prompt.arguments is not None
            assert len(prompt.arguments) == 2
            assert prompt.arguments[0].name == "name"
            assert prompt.arguments[0].required is True
            assert prompt.arguments[1].name == "optional"
            assert prompt.arguments[1].required is False

    @pytest.mark.anyio
    async def test_get_prompt(self):
        """Test getting a prompt through MCP protocol."""
        mcp = FastMCP()

        @mcp.prompt()
        def fn(name: str) -> str:
            return f"Hello, {name}!"

        async with client_session(mcp._mcp_server) as client:
            result = await client.get_prompt("fn", {"name": "World"})
            assert len(result.messages) == 1
            message = result.messages[0]
            assert message.role == "user"
            content = message.content
            assert isinstance(content, TextContent)
            assert content.text == "Hello, World!"

    @pytest.mark.anyio
    async def test_get_prompt_with_resource(self):
        """Test getting a prompt that returns resource content."""
        mcp = FastMCP()

        @mcp.prompt()
        def fn() -> Message:
            return UserMessage(
                content=EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(
                        uri=AnyUrl("file://file.txt"),
                        text="File contents",
                        mimeType="text/plain",
                    ),
                )
            )

        async with client_session(mcp._mcp_server) as client:
            result = await client.get_prompt("fn")
            assert len(result.messages) == 1
            message = result.messages[0]
            assert message.role == "user"
            content = message.content
            assert isinstance(content, EmbeddedResource)
            resource = content.resource
            assert isinstance(resource, TextResourceContents)
            assert resource.text == "File contents"
            assert resource.mimeType == "text/plain"

    @pytest.mark.anyio
    async def test_get_unknown_prompt(self):
        """Test error when getting unknown prompt."""
        mcp = FastMCP()
        async with client_session(mcp._mcp_server) as client:
            with pytest.raises(McpError, match="Unknown prompt"):
                await client.get_prompt("unknown")

    @pytest.mark.anyio
    async def test_get_prompt_missing_args(self):
        """Test error when required arguments are missing."""
        mcp = FastMCP()

        @mcp.prompt()
        def prompt_fn(name: str) -> str:
            return f"Hello, {name}!"

        async with client_session(mcp._mcp_server) as client:
            with pytest.raises(McpError, match="Missing required arguments"):
                await client.get_prompt("prompt_fn")
