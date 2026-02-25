"""
FatSecret MCP Server - Remote MCP server for food tracking via FatSecret API.

Provides tools to search foods, log diary entries, view daily intake,
and manage food entries through the FatSecret Platform API.
"""

import json
import os
import hashlib
import hmac
import time
import urllib.parse
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, ConfigDict, field_validator
from enum import Enum


# ── Configuration ────────────────────────────────────────────────────────────

FATSECRET_API_URL = "https://platform.fatsecret.com/rest/server.api"
FATSECRET_AUTH_URL = "https://www.fatsecret.com/oauth/authorize"
FATSECRET_REQUEST_TOKEN_URL = "https://www.fatsecret.com/oauth/request_token"
FATSECRET_ACCESS_TOKEN_URL = "https://www.fatsecret.com/oauth/access_token"


# ── OAuth 1.0 Implementation ────────────────────────────────────────────────

class OAuth1Client:
    """Handles OAuth 1.0a HMAC-SHA1 signed requests for FatSecret API."""

    def __init__(self, consumer_key: str, consumer_secret: str,
                 access_token: str = "", token_secret: str = ""):
        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        self.access_token = access_token
        self.token_secret = token_secret

    def _generate_nonce(self) -> str:
        return hashlib.md5(str(time.time()).encode()).hexdigest()

    def _sign(self, method: str, url: str, params: dict) -> str:
        """Generate OAuth 1.0 HMAC-SHA1 signature."""
        sorted_params = sorted(params.items())
        param_string = "&".join(f"{self._percent_encode(k)}={self._percent_encode(str(v))}"
                                for k, v in sorted_params)

        base_string = "&".join([
            method.upper(),
            self._percent_encode(url),
            self._percent_encode(param_string)
        ])

        signing_key = f"{self._percent_encode(self.consumer_secret)}&{self._percent_encode(self.token_secret)}"

        signature = hmac.new(
            signing_key.encode("utf-8"),
            base_string.encode("utf-8"),
            hashlib.sha1
        ).digest()

        import base64
        return base64.b64encode(signature).decode("utf-8")

    @staticmethod
    def _percent_encode(s: str) -> str:
        return urllib.parse.quote(str(s), safe="")

    def get_oauth_params(self) -> dict:
        params = {
            "oauth_consumer_key": self.consumer_key,
            "oauth_nonce": self._generate_nonce(),
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp": str(int(time.time())),
            "oauth_version": "1.0",
        }
        if self.access_token:
            params["oauth_token"] = self.access_token
        return params

    async def request(self, method: str, url: str, params: dict,
                      client: httpx.AsyncClient) -> dict:
        """Make an OAuth 1.0 signed request."""
        oauth_params = self.get_oauth_params()
        all_params = {**params, **oauth_params}
        signature = self._sign(method, url, all_params)
        all_params["oauth_signature"] = signature

        if method.upper() == "GET":
            response = await client.get(url, params=all_params)
        else:
            response = await client.post(url, data=all_params)

        response.raise_for_status()

        # Handle both JSON and URL-encoded responses
        content_type = response.headers.get("content-type", "")
        if "json" in content_type or response.text.startswith("{"):
            return response.json()
        else:
            return dict(urllib.parse.parse_qsl(response.text))

    async def get_request_token(self, client: httpx.AsyncClient,
                                callback_url: str = "oob") -> dict:
        """Get OAuth request token for user authorization."""
        params = {"oauth_callback": callback_url}
        oauth_params = self.get_oauth_params()
        all_params = {**params, **oauth_params}
        signature = self._sign("POST", FATSECRET_REQUEST_TOKEN_URL, all_params)
        all_params["oauth_signature"] = signature

        response = await client.post(FATSECRET_REQUEST_TOKEN_URL, data=all_params)
        response.raise_for_status()
        return dict(urllib.parse.parse_qsl(response.text))

    async def get_access_token(self, client: httpx.AsyncClient,
                               request_token: str, request_secret: str,
                               verifier: str) -> dict:
        """Exchange request token + verifier for access token."""
        self.access_token = request_token
        self.token_secret = request_secret

        params = {"oauth_verifier": verifier}
        oauth_params = self.get_oauth_params()
        all_params = {**params, **oauth_params}
        signature = self._sign("POST", FATSECRET_ACCESS_TOKEN_URL, all_params)
        all_params["oauth_signature"] = signature

        response = await client.post(FATSECRET_ACCESS_TOKEN_URL, data=all_params)
        response.raise_for_status()
        return dict(urllib.parse.parse_qsl(response.text))


# ── FatSecret API Client ────────────────────────────────────────────────────

class FatSecretClient:
    """High-level client for FatSecret Platform API."""

    def __init__(self, consumer_key: str, consumer_secret: str,
                 access_token: str = "", token_secret: str = ""):
        self.oauth = OAuth1Client(consumer_key, consumer_secret,
                                  access_token, token_secret)
        self.http_client = httpx.AsyncClient(timeout=30.0)

    async def close(self):
        await self.http_client.aclose()

    def is_authenticated(self) -> bool:
        return bool(self.oauth.access_token and self.oauth.token_secret)

    async def api_call(self, method: str, **kwargs) -> dict:
        """Make an authenticated API call to FatSecret."""
        params = {"method": method, "format": "json", **kwargs}
        return await self.oauth.request("GET", FATSECRET_API_URL, params,
                                        self.http_client)

    # ── Public API methods (no user auth required) ───────────────────────

    async def search_foods(self, query: str, page: int = 0,
                           max_results: int = 10) -> dict:
        return await self.api_call(
            "foods.search",
            search_expression=query,
            page_number=str(page),
            max_results=str(max_results)
        )

    async def get_food(self, food_id: str) -> dict:
        return await self.api_call("food.get.v4", food_id=food_id)

    # ── User-authenticated API methods ───────────────────────────────────

    async def create_food_entry(self, food_id: str, food_name: str,
                                serving_id: str, number_of_units: float,
                                meal: str, date: Optional[str] = None) -> dict:
        params = {
            "food_id": food_id,
            "food_entry_name": food_name,
            "serving_id": serving_id,
            "number_of_units": str(number_of_units),
            "meal": meal,
        }
        if date:
            params["date"] = date
        return await self.api_call("food_entry.create", **params)

    async def get_food_entries(self, date: Optional[str] = None) -> dict:
        params = {}
        if date:
            params["date"] = date
        return await self.api_call("food_entries.get", **params)

    async def delete_food_entry(self, food_entry_id: str) -> dict:
        return await self.api_call("food_entry.delete",
                                   food_entry_id=food_entry_id)

    async def get_food_entries_month(self, date: Optional[str] = None) -> dict:
        params = {}
        if date:
            params["date"] = date
        return await self.api_call("food_entries.get_month", **params)

    # ── OAuth flow helpers ───────────────────────────────────────────────

    async def start_auth(self, callback_url: str = "oob") -> dict:
        result = await self.oauth.get_request_token(self.http_client, callback_url)
        return {
            "request_token": result["oauth_token"],
            "request_secret": result["oauth_token_secret"],
            "auth_url": f"{FATSECRET_AUTH_URL}?oauth_token={result['oauth_token']}"
        }

    async def complete_auth(self, request_token: str, request_secret: str,
                            verifier: str) -> dict:
        result = await self.oauth.get_access_token(
            self.http_client, request_token, request_secret, verifier
        )
        self.oauth.access_token = result["oauth_token"]
        self.oauth.token_secret = result["oauth_token_secret"]
        return {
            "access_token": result["oauth_token"],
            "token_secret": result["oauth_token_secret"]
        }


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def app_lifespan():
    """Initialize FatSecret client on server startup."""
    consumer_key = os.environ.get("FATSECRET_CONSUMER_KEY", "")
    consumer_secret = os.environ.get("FATSECRET_CONSUMER_SECRET", "")
    access_token = os.environ.get("FATSECRET_ACCESS_TOKEN", "")
    token_secret = os.environ.get("FATSECRET_TOKEN_SECRET", "")

    if not consumer_key or not consumer_secret:
        raise ValueError(
            "FATSECRET_CONSUMER_KEY and FATSECRET_CONSUMER_SECRET "
            "environment variables are required."
        )

    client = FatSecretClient(consumer_key, consumer_secret,
                             access_token, token_secret)

    # Store pending auth state for OAuth flow
    yield {"client": client, "pending_auth": {}}

    await client.close()


# ── MCP Server ───────────────────────────────────────────────────────────────

port = int(os.environ.get("PORT", os.environ.get("MCP_PORT", "8000")))

mcp = FastMCP(
    "fatsecret_mcp",
    lifespan=app_lifespan,
    host="0.0.0.0",
    port=port,
    stateless_http=True,
)


# ── Helper functions ─────────────────────────────────────────────────────────

def _get_client(ctx) -> FatSecretClient:
    return ctx.request_context.lifespan_state["client"]


def _get_pending_auth(ctx) -> dict:
    return ctx.request_context.lifespan_state["pending_auth"]


def _handle_api_error(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        status = e.response.status_code
        if status == 401:
            return ("Error: Authentication failed. Please run "
                    "fatsecret_start_auth to connect your FatSecret account.")
        elif status == 404:
            return "Error: Resource not found. Please check the ID is correct."
        elif status == 429:
            return "Error: Rate limit exceeded. Please wait before retrying."
        return f"Error: API request failed with status {status}: {e.response.text}"
    elif isinstance(e, httpx.TimeoutException):
        return "Error: Request timed out. Please try again."
    return f"Error: {type(e).__name__}: {str(e)}"


def _format_food_result(food: dict) -> str:
    """Format a single food search result as readable text."""
    name = food.get("food_name", "Unknown")
    brand = food.get("brand_name", "")
    food_id = food.get("food_id", "")
    desc = food.get("food_description", "")

    header = f"**{name}**" + (f" ({brand})" if brand else "")
    return f"{header}\n  ID: {food_id}\n  {desc}"


def _format_serving(serving: dict) -> str:
    """Format a serving option as readable text."""
    sid = serving.get("serving_id", "")
    desc = serving.get("serving_description", "")
    cal = serving.get("calories", "?")
    protein = serving.get("protein", "?")
    carbs = serving.get("carbohydrate", "?")
    fat = serving.get("fat", "?")
    return (f"  Serving ID: {sid} — {desc}\n"
            f"    Calories: {cal} | Protein: {protein}g | "
            f"Carbs: {carbs}g | Fat: {fat}g")


def _days_since_epoch(date_str: Optional[str]) -> Optional[str]:
    """Convert YYYY-MM-DD to days since Unix epoch (FatSecret date format)."""
    if not date_str:
        return None
    from datetime import datetime
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    epoch = datetime(1970, 1, 1)
    return str((dt - epoch).days)


# ── Input Models ─────────────────────────────────────────────────────────────

class MealType(str, Enum):
    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    DINNER = "dinner"
    OTHER = "other"


class SearchFoodsInput(BaseModel):
    """Input for searching the FatSecret food database."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(
        ..., description="Search query (e.g., 'Greek yogurt', 'chicken breast')",
        min_length=1, max_length=200
    )
    page: int = Field(
        default=0, description="Page number for pagination (0-indexed)", ge=0
    )
    max_results: int = Field(
        default=10, description="Number of results per page", ge=1, le=50
    )


class GetFoodInput(BaseModel):
    """Input for getting detailed food information."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    food_id: str = Field(
        ..., description="FatSecret food ID (from search results)", min_length=1
    )


class LogFoodInput(BaseModel):
    """Input for logging a food entry to the diary."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    food_id: str = Field(..., description="FatSecret food ID")
    food_name: str = Field(..., description="Display name for the entry", min_length=1)
    serving_id: str = Field(..., description="Serving ID (from food details)")
    number_of_units: float = Field(
        ..., description="Number of servings (e.g., 1.5 for 1.5 servings)",
        gt=0, le=1000
    )
    meal: MealType = Field(
        ..., description="Meal type: breakfast, lunch, dinner, or other"
    )
    date: Optional[str] = Field(
        default=None,
        description="Date in YYYY-MM-DD format (defaults to today)",
        pattern=r"^\d{4}-\d{2}-\d{2}$"
    )


class GetDiaryInput(BaseModel):
    """Input for viewing food diary entries."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    date: Optional[str] = Field(
        default=None,
        description="Date in YYYY-MM-DD format (defaults to today)",
        pattern=r"^\d{4}-\d{2}-\d{2}$"
    )


class DeleteFoodEntryInput(BaseModel):
    """Input for deleting a food diary entry."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    food_entry_id: str = Field(
        ..., description="The food entry ID to delete (from diary listing)"
    )


class CompleteAuthInput(BaseModel):
    """Input for completing OAuth authorization."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    pin: str = Field(
        ..., description="The PIN/verifier code from the FatSecret authorization page",
        min_length=1
    )


# ── Tools ────────────────────────────────────────────────────────────────────

@mcp.tool(
    name="fatsecret_search_foods",
    annotations={
        "title": "Search Foods",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def fatsecret_search_foods(params: SearchFoodsInput, ctx=None) -> str:
    """Search the FatSecret food database for foods matching a query.

    Returns a list of matching foods with their IDs, names, and basic
    nutritional summaries. Use the food_id from results with
    fatsecret_get_food to see detailed serving/nutrition info.

    Args:
        params: Search parameters including query string and pagination.

    Returns:
        str: Formatted list of matching foods with IDs and nutrition summaries.
    """
    client = _get_client(ctx)
    try:
        result = await client.search_foods(
            params.query, params.page, params.max_results
        )
        foods = result.get("foods", {})
        if not foods or "food" not in foods:
            return f"No foods found matching '{params.query}'."

        food_list = foods["food"]
        if isinstance(food_list, dict):
            food_list = [food_list]

        total = foods.get("total_results", len(food_list))
        page = foods.get("page_number", params.page)

        lines = [f"## Search results for '{params.query}' "
                 f"(page {page}, {total} total)\n"]
        for f in food_list:
            lines.append(_format_food_result(f))
            lines.append("")

        lines.append(f"*Showing {len(food_list)} of {total} results. "
                     f"Use page={int(page)+1} for more.*")
        return "\n".join(lines)

    except Exception as e:
        return _handle_api_error(e)


@mcp.tool(
    name="fatsecret_get_food",
    annotations={
        "title": "Get Food Details",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def fatsecret_get_food(params: GetFoodInput, ctx=None) -> str:
    """Get detailed nutritional information and serving options for a food.

    Returns all available serving sizes with full macro/micronutrient data.
    Use the serving_id from the results when logging food entries.

    Args:
        params: Food ID to look up.

    Returns:
        str: Detailed food info with all serving options and nutrition data.
    """
    client = _get_client(ctx)
    try:
        result = await client.get_food(params.food_id)
        food = result.get("food", {})
        name = food.get("food_name", "Unknown")
        brand = food.get("brand_name", "")

        header = f"## {name}" + (f" ({brand})" if brand else "")
        lines = [header, f"Food ID: {food.get('food_id', '')}", "", "### Servings:"]

        servings = food.get("servings", {}).get("serving", [])
        if isinstance(servings, dict):
            servings = [servings]

        for s in servings:
            lines.append(_format_serving(s))
            lines.append("")

        return "\n".join(lines)

    except Exception as e:
        return _handle_api_error(e)


@mcp.tool(
    name="fatsecret_log_food",
    annotations={
        "title": "Log Food Entry",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def fatsecret_log_food(params: LogFoodInput, ctx=None) -> str:
    """Log a food entry to the user's FatSecret food diary.

    Requires user authentication. First search for the food, get its details
    to find the correct serving_id, then log it with this tool.

    Args:
        params: Food entry details including food_id, serving_id, quantity, and meal.

    Returns:
        str: Confirmation of the logged entry or error message.
    """
    client = _get_client(ctx)
    if not client.is_authenticated():
        return ("Error: Not authenticated. Please run fatsecret_start_auth "
                "first to connect your FatSecret account.")

    try:
        date_param = _days_since_epoch(params.date) if params.date else None
        result = await client.create_food_entry(
            food_id=params.food_id,
            food_name=params.food_name,
            serving_id=params.serving_id,
            number_of_units=params.number_of_units,
            meal=params.meal.value,
            date=date_param,
        )

        entry_id = result.get("food_entry_id", {}).get("value", "unknown")
        return (f"✅ Logged: {params.number_of_units}x {params.food_name} "
                f"for {params.meal.value}\n"
                f"Entry ID: {entry_id}")

    except Exception as e:
        return _handle_api_error(e)


@mcp.tool(
    name="fatsecret_get_diary",
    annotations={
        "title": "View Food Diary",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def fatsecret_get_diary(params: GetDiaryInput, ctx=None) -> str:
    """View food diary entries for a given date.

    Returns all logged food entries with nutritional totals.
    Requires user authentication.

    Args:
        params: Optional date filter (defaults to today).

    Returns:
        str: Formatted diary entries grouped by meal with totals.
    """
    client = _get_client(ctx)
    if not client.is_authenticated():
        return ("Error: Not authenticated. Please run fatsecret_start_auth "
                "first to connect your FatSecret account.")

    try:
        date_param = _days_since_epoch(params.date) if params.date else None
        result = await client.get_food_entries(date=date_param)

        entries = result.get("food_entries", {}).get("food_entry", [])
        if isinstance(entries, dict):
            entries = [entries]

        if not entries:
            date_display = params.date or "today"
            return f"No food entries found for {date_display}."

        # Group by meal
        meals: Dict[str, list] = {}
        total_cal = 0.0
        total_protein = 0.0
        total_carbs = 0.0
        total_fat = 0.0

        for entry in entries:
            meal = entry.get("meal", "other")
            if meal not in meals:
                meals[meal] = []
            meals[meal].append(entry)

            total_cal += float(entry.get("calories", 0))
            total_protein += float(entry.get("protein", 0))
            total_carbs += float(entry.get("carbohydrate", 0))
            total_fat += float(entry.get("fat", 0))

        date_display = params.date or "Today"
        lines = [f"## Food Diary — {date_display}\n"]

        meal_order = ["breakfast", "lunch", "dinner", "other"]
        for meal_name in meal_order:
            if meal_name not in meals:
                continue
            lines.append(f"### {meal_name.capitalize()}")
            for e in meals[meal_name]:
                name = e.get("food_entry_name", "Unknown")
                cal = e.get("calories", "?")
                eid = e.get("food_entry_id", "")
                serving = e.get("serving_id", "")
                units = e.get("number_of_units", "")
                lines.append(
                    f"  - {name} — {cal} cal "
                    f"(entry_id: {eid})"
                )
            lines.append("")

        lines.append("### Daily Totals")
        lines.append(f"  Calories: {total_cal:.0f}")
        lines.append(f"  Protein: {total_protein:.1f}g")
        lines.append(f"  Carbs: {total_carbs:.1f}g")
        lines.append(f"  Fat: {total_fat:.1f}g")

        return "\n".join(lines)

    except Exception as e:
        return _handle_api_error(e)


@mcp.tool(
    name="fatsecret_delete_entry",
    annotations={
        "title": "Delete Food Entry",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def fatsecret_delete_entry(params: DeleteFoodEntryInput, ctx=None) -> str:
    """Delete a food entry from the diary.

    Requires user authentication. Get entry IDs from fatsecret_get_diary.

    Args:
        params: The food entry ID to delete.

    Returns:
        str: Confirmation of deletion or error message.
    """
    client = _get_client(ctx)
    if not client.is_authenticated():
        return ("Error: Not authenticated. Please run fatsecret_start_auth "
                "first to connect your FatSecret account.")

    try:
        await client.delete_food_entry(params.food_entry_id)
        return f"✅ Deleted food entry {params.food_entry_id}."
    except Exception as e:
        return _handle_api_error(e)


@mcp.tool(
    name="fatsecret_start_auth",
    annotations={
        "title": "Start FatSecret Authorization",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def fatsecret_start_auth(ctx=None) -> str:
    """Start the OAuth authorization flow to connect a FatSecret account.

    Returns a URL the user must visit to authorize the app. After authorizing,
    the user receives a PIN code which should be provided to
    fatsecret_complete_auth.

    Returns:
        str: Authorization URL and instructions.
    """
    client = _get_client(ctx)
    pending = _get_pending_auth(ctx)

    try:
        auth_info = await client.start_auth()
        pending["request_token"] = auth_info["request_token"]
        pending["request_secret"] = auth_info["request_secret"]

        return (
            f"## FatSecret Authorization\n\n"
            f"Please visit this URL to authorize the app:\n\n"
            f"**{auth_info['auth_url']}**\n\n"
            f"After authorizing, you'll receive a PIN code. "
            f"Use `fatsecret_complete_auth` with that PIN to finish setup."
        )
    except Exception as e:
        return _handle_api_error(e)


@mcp.tool(
    name="fatsecret_complete_auth",
    annotations={
        "title": "Complete FatSecret Authorization",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def fatsecret_complete_auth(params: CompleteAuthInput, ctx=None) -> str:
    """Complete the OAuth authorization with the PIN from FatSecret.

    After visiting the auth URL from fatsecret_start_auth, enter the
    PIN code here to complete the connection.

    Args:
        params: The PIN/verifier code from the authorization page.

    Returns:
        str: Success message with access tokens to save, or error.
    """
    client = _get_client(ctx)
    pending = _get_pending_auth(ctx)

    if "request_token" not in pending:
        return ("Error: No pending authorization. "
                "Please run fatsecret_start_auth first.")

    try:
        tokens = await client.complete_auth(
            request_token=pending["request_token"],
            request_secret=pending["request_secret"],
            verifier=params.pin,
        )

        # Clear pending state
        pending.clear()

        return (
            f"✅ Authorization successful! Your FatSecret account is now connected.\n\n"
            f"**Save these tokens as environment variables for persistent access:**\n"
            f"```\n"
            f"FATSECRET_ACCESS_TOKEN={tokens['access_token']}\n"
            f"FATSECRET_TOKEN_SECRET={tokens['token_secret']}\n"
            f"```\n\n"
            f"You can now use fatsecret_search_foods, fatsecret_log_food, "
            f"and fatsecret_get_diary."
        )
    except Exception as e:
        return _handle_api_error(e)


@mcp.tool(
    name="fatsecret_auth_status",
    annotations={
        "title": "Check Auth Status",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def fatsecret_auth_status(ctx=None) -> str:
    """Check whether the server is authenticated with a FatSecret user account.

    Returns:
        str: Current authentication status.
    """
    client = _get_client(ctx)
    if client.is_authenticated():
        return "✅ Authenticated — ready to log food and view diary."
    else:
        return ("❌ Not authenticated. Run fatsecret_start_auth to connect "
                "your FatSecret account.")


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    transport = os.environ.get("MCP_TRANSPORT", "streamable-http")

    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        # Monkey-patch the Starlette app to add health and discovery routes
        _original_app = mcp.streamable_http_app

        def patched_app():
            from starlette.responses import JSONResponse, Response
            from starlette.routing import Route
            from starlette.middleware.cors import CORSMiddleware

            app = _original_app()

            async def health(request):
                return JSONResponse({"status": "ok"})

            async def well_known_oauth_protected_resource(request):
                # Return 404 to signal this is an authless server
                return Response(status_code=404)

            async def well_known_oauth_authorization_server(request):
                # Return 404 to signal this is an authless server
                return Response(status_code=404)

            # Prepend routes
            app.routes.insert(0, Route("/health", health, methods=["GET"]))
            app.routes.insert(0, Route(
                "/.well-known/oauth-protected-resource",
                well_known_oauth_protected_resource,
                methods=["GET"]
            ))
            app.routes.insert(0, Route(
                "/.well-known/oauth-authorization-server",
                well_known_oauth_authorization_server,
                methods=["GET"]
            ))

            # Add CORS middleware
            app.add_middleware(
                CORSMiddleware,
                allow_origins=["https://claude.ai", "https://claude.com"],
                allow_methods=["GET", "POST", "DELETE", "OPTIONS", "HEAD"],
                allow_headers=["*"],
                expose_headers=["mcp-session-id"],
            )

            return app

        mcp.streamable_http_app = patched_app

        print(f"Starting FatSecret MCP server on port {port}", file=sys.stderr)
        mcp.run(transport="streamable-http")
