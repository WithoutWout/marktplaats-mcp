"""Marktplaats MCP Server - Search and browse Marktplaats.nl listings."""

import json
import re
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

import requests
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP

# Initialize the MCP server
mcp = FastMCP("marktplaats")

# Constants
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

HTML_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
}

SEARCH_URL = "https://www.marktplaats.nl/lrp/api/search"
SELLER_URL = "https://www.marktplaats.nl/v/api/seller-profile"
LISTING_URL = "https://link.marktplaats.nl"

# Traits that indicate a business seller
BUSINESS_TRAITS = {
    "ADMARKT_CONSOLE",
    "CUSTOMER_SUPPORT_BUSINESS_LINE",
    "SELLER_PROFILE_URL",
    "VERIFIED_SELLER",
    "UNIQUE_SELLING_POINTS",
    "SHOPPING_CART",
}

# Category data (commonly used categories)
L1_CATEGORIES = {
    "antiek en kunst": 1,
    "audio, tv en foto": 31,
    "auto's": 91,
    "auto-onderdelen": 2600,
    "auto diversen": 48,
    "boeken": 201,
    "caravans en kamperen": 289,
    "cd's en dvd's": 1744,
    "computers en software": 322,
    "contacten en berichten": 378,
    "diensten en vakmensen": 1098,
    "dieren en toebehoren": 395,
    "doe-het-zelf en verbouw": 239,
    "fietsen en brommers": 445,
    "hobby en vrije tijd": 1099,
    "huis en inrichting": 504,
    "huizen en kamers": 1032,
    "kinderen en baby's": 565,
    "kleding | dames": 621,
    "kleding | heren": 1776,
    "motoren": 678,
    "muziek en instrumenten": 728,
    "postzegels en munten": 1784,
    "sieraden, tassen en uiterlijk": 1826,
    "spelcomputers en games": 356,
    "sport en fitness": 784,
    "telecommunicatie": 820,
    "tickets en kaartjes": 1984,
    "tuin en terras": 1847,
    "vacatures": 167,
    "vakantie": 856,
    "verzamelen": 895,
    "watersport en boten": 976,
    "witgoed en apparatuur": 537,
    "zakelijke goederen": 1085,
    "diversen": 428,
}

# Common subcategories
L2_CATEGORIES = {
    # Computers
    "laptops": {"id": 339, "parent": 322},
    "desktops": {"id": 340, "parent": 322},
    "tablets": {"id": 2097, "parent": 322},
    # Fietsen
    "fietsen | dames": {"id": 446, "parent": 445},
    "fietsen | heren": {"id": 447, "parent": 445},
    "elektrische fietsen": {"id": 1901, "parent": 445},
    "kinderfietsen": {"id": 449, "parent": 445},
    # Telefoons
    "mobiele telefoons": {"id": 821, "parent": 820},
    "iphone": {"id": 1953, "parent": 820},
    "samsung": {"id": 1954, "parent": 820},
    # Auto's
    "bmw": {"id": 92, "parent": 91},
    "volkswagen": {"id": 127, "parent": 91},
    "audi": {"id": 95, "parent": 91},
    "mercedes-benz": {"id": 113, "parent": 91},
}


class SortBy(str, Enum):
    DATE = "SORT_INDEX"
    PRICE = "PRICE"
    OPTIMIZED = "OPTIMIZED"
    LOCATION = "LOCATION"


class SortOrder(str, Enum):
    ASC = "INCREASING"
    DESC = "DECREASING"


class Condition(int, Enum):
    NEW = 30
    REFURBISHED = 14050
    AS_GOOD_AS_NEW = 31
    USED = 32
    NOT_WORKING = 13940


def _get_request(url: str, params: dict | None = None, headers: dict | None = None) -> requests.Response:
    """Make a GET request with appropriate headers."""
    return requests.get(
        url,
        params=params,
        headers=headers or REQUEST_HEADERS,
        timeout=15,
    )


def _parse_price_type(price_type: str, price_cents: int) -> str:
    """Convert price type and cents to readable string."""
    price_map = {
        "FIXED": f"€ {price_cents / 100:,.2f}",
        "BID": "Bieden",
        "BID_FROM": f"Bieden vanaf € {price_cents / 100:,.2f}",
        "FREE": "Gratis",
        "RESERVED": "Gereserveerd",
        "SEE_DESCRIPTION": "Zie omschrijving",
        "TO_BE_AGREED_UPON": "N.o.t.k.",
        "ON_REQUEST": "Op aanvraag",
        "EXCHANGE": "Ruilen",
    }
    return price_map.get(price_type, f"€ {price_cents / 100:,.2f}")


def _detect_seller_type(traits: list[str]) -> str:
    """Detect if seller is business or private based on traits."""
    trait_set = set(traits)
    if trait_set & BUSINESS_TRAITS:
        return "business"
    return "private"


def _extract_specs_from_description(description: str, title: str = "") -> dict[str, str]:
    """Extract hardware specs from description text for laptops/tablets."""
    specs = {}
    text = f"{title} {description}".lower()

    # RAM patterns
    ram_patterns = [
        r'(\d+)\s*gb\s*ram',
        r'ram[:\s]*(\d+)\s*gb',
        r'(\d+)gb\s*geheugen',
        r'werkgeheugen[:\s]*(\d+)\s*gb',
    ]
    for pattern in ram_patterns:
        match = re.search(pattern, text)
        if match:
            specs["ram"] = f"{match.group(1)}GB"
            break

    # Storage patterns
    storage_patterns = [
        r'(\d+)\s*gb\s*ssd',
        r'(\d+)\s*tb\s*ssd',
        r'ssd[:\s]*(\d+)\s*gb',
        r'(\d+)\s*gb\s*opslag',
        r'(\d+)\s*tb\s*opslag',
        r'(\d+)\s*gb\s*hdd',
        r'(\d+)\s*tb\s*hdd',
    ]
    for pattern in storage_patterns:
        match = re.search(pattern, text)
        if match:
            size = match.group(1)
            storage_type = "SSD" if "ssd" in pattern else "HDD" if "hdd" in pattern else ""
            unit = "TB" if "tb" in pattern else "GB"
            specs["storage"] = f"{size}{unit} {storage_type}".strip()
            break

    # CPU patterns
    cpu_patterns = [
        r'(i[3579][-\s]?\d{4,5}\w*)',
        r'(intel\s+core\s+i[3579])',
        r'(ryzen\s*[3579]\s*\d{4}\w*)',
        r'(m[123]\s*(pro|max)?)',
        r'(apple\s+m[123])',
    ]
    for pattern in cpu_patterns:
        match = re.search(pattern, text)
        if match:
            specs["cpu"] = match.group(1).strip().upper()
            break

    # Screen size
    screen_patterns = [
        r"(\d{2})['\"]?\s*inch",
        r"(\d{2})[,.]?\d?\s*inch",
        r"scherm[:\s]*(\d{2})",
    ]
    for pattern in screen_patterns:
        match = re.search(pattern, text)
        if match:
            specs["screen"] = f'{match.group(1)}"'
            break

    return specs


def _format_listing(listing: dict, include_specs: bool = False) -> dict:
    """Format a listing from API response to a clean dict."""
    price_info = listing.get("priceInfo", {})
    location = listing.get("location", {})
    seller = listing.get("sellerInformation", {})
    traits = listing.get("traits", [])

    # Get first image if available
    pictures = listing.get("pictures", [])
    first_image = pictures[0].get("mediumUrl", "") if pictures else ""
    if first_image and not first_image.startswith("http"):
        first_image = "https:" + first_image

    # Distance handling - only show if valid (>= 0)
    distance_meters = location.get("distanceMeters")
    distance_km = None
    if distance_meters is not None and distance_meters >= 0:
        distance_km = round(distance_meters / 1000, 1)

    description = listing.get("description", "")
    title = listing.get("title", "")

    result = {
        "id": listing.get("itemId"),
        "title": title,
        "description": description[:200] + "..." if len(description) > 200 else description,
        "price": _parse_price_type(price_info.get("priceType", ""), price_info.get("priceCents", 0)),
        "price_cents": price_info.get("priceCents", 0),
        "condition": next((attr.get("value") for attr in listing.get("attributes", []) if attr.get("key") == "condition"), None),
        "location": {
            "city": location.get("cityName"),
            "distance_km": distance_km,
        },
        "seller": {
            "id": seller.get("sellerId"),
            "name": seller.get("sellerName"),
            "is_verified": seller.get("isVerified", False),
            "type": _detect_seller_type(traits),
        },
        "date": listing.get("date"),
        "image": first_image,
        "link": f"https://link.marktplaats.nl/{listing.get('itemId')}",
    }

    # Extract specs for electronics
    if include_specs:
        specs = _extract_specs_from_description(description, title)
        if specs:
            result["specs"] = specs

    return result


@mcp.tool()
def search_listings(
    query: str = "",
    category: str | None = None,
    subcategory: str | None = None,
    zip_code: str = "",
    distance_km: int = 1000,
    price_from: int | None = None,
    price_to: int | None = None,
    condition: str | None = None,
    seller_type: str | None = None,
    sort_by: str = "optimized",
    sort_order: str = "asc",
    limit: int = 10,
    offset: int = 0,
    offered_since_days: int | None = None,
    attribute_ids: list[int] | None = None,
    extract_specs: bool = False,
) -> dict[str, Any]:
    """
    Search for listings on Marktplaats.nl.

    Args:
        query: Search query text (required if no category specified)
        category: Main category name (e.g., "computers en software", "fietsen en brommers")
        subcategory: Subcategory name (e.g., "laptops", "elektrische fietsen")
        zip_code: Dutch postal code for distance calculations (e.g., "1016LV"). Required for distance filtering!
        distance_km: Maximum distance in kilometers (default: 1000). Only works with zip_code.
        price_from: Minimum price in euros
        price_to: Maximum price in euros
        condition: Item condition: "new", "as_good_as_new", "used", "refurbished", "not_working"
        seller_type: Filter by seller type: "business" (zakelijk, for VAT invoices) or "private" (particulier)
        sort_by: Sort method: "date", "price", "optimized", "location"
        sort_order: Sort order: "asc" or "desc"
        limit: Number of results (1-100, default: 10)
        offset: Pagination offset
        offered_since_days: Only show items posted within the last X days
        attribute_ids: List of attribute filter IDs (use get_category_filters to find these)
        extract_specs: Try to extract hardware specs (RAM, storage, CPU) from descriptions (for laptops/tablets)

    Returns:
        Dictionary with total_count, returned_count, and list of listings
    """
    if not query and not category and not subcategory:
        return {"error": "Please provide a search query or category"}

    # Build params
    params: dict[str, Any] = {
        "limit": str(min(max(1, limit), 100)),
        "offset": str(offset),
        "query": query,
        "searchInTitleAndDescription": "true",
        "viewOptions": "list-view",
        "distanceMeters": str(distance_km * 1000),
        "postcode": zip_code,
        "sortBy": SortBy[sort_by.upper()].value if sort_by.upper() in SortBy.__members__ else SortBy.OPTIMIZED.value,
        "sortOrder": SortOrder[sort_order.upper()].value if sort_order.upper() in SortOrder.__members__ else SortOrder.ASC.value,
    }

    # Category handling
    if subcategory:
        subcat_lower = subcategory.lower()
        if subcat_lower in L2_CATEGORIES:
            params["l2CategoryId"] = str(L2_CATEGORIES[subcat_lower]["id"])
            params["l1CategoryId"] = str(L2_CATEGORIES[subcat_lower]["parent"])
        else:
            return {"error": f"Unknown subcategory: {subcategory}. Use list_categories to see available categories."}
    elif category:
        cat_lower = category.lower()
        if cat_lower in L1_CATEGORIES:
            params["l1CategoryId"] = str(L1_CATEGORIES[cat_lower])
        else:
            return {"error": f"Unknown category: {category}. Use list_categories to see available categories."}

    # Price filter
    if price_from is not None or price_to is not None:
        price_from_cents = str(price_from * 100) if price_from is not None else "null"
        price_to_cents = str(price_to * 100) if price_to is not None else "null"
        params["attributeRanges[]"] = [f"PriceCents:{price_from_cents}:{price_to_cents}"]

    # Condition filter
    condition_map = {
        "new": Condition.NEW.value,
        "as_good_as_new": Condition.AS_GOOD_AS_NEW.value,
        "used": Condition.USED.value,
        "refurbished": Condition.REFURBISHED.value,
        "not_working": Condition.NOT_WORKING.value,
    }

    attribute_list = []
    if condition and condition.lower() in condition_map:
        attribute_list.append(condition_map[condition.lower()])

    if attribute_ids:
        attribute_list.extend(attribute_ids)

    if attribute_list:
        params["attributesById[]"] = attribute_list

    # Date filter
    if offered_since_days:
        since = datetime.now() - timedelta(days=offered_since_days)
        params["attributesByKey[]"] = [f"offeredSince:{int(since.timestamp()) * 1000}"]

    # Make request
    try:
        response = _get_request(SEARCH_URL, params)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        return {"error": f"Request failed: {str(e)}"}
    except json.JSONDecodeError:
        return {"error": "Invalid response from Marktplaats"}

    # Format results
    listings = [_format_listing(listing, include_specs=extract_specs) for listing in data.get("listings", [])]

    # Filter by seller type if requested
    if seller_type:
        seller_type_lower = seller_type.lower()
        if seller_type_lower in ("business", "zakelijk"):
            listings = [l for l in listings if l["seller"]["type"] == "business"]
        elif seller_type_lower in ("private", "particulier"):
            listings = [l for l in listings if l["seller"]["type"] == "private"]

    total_count = data.get("totalResultCount", 0)

    result = {
        "total_count": total_count,
        "returned_count": len(listings),
        "offset": offset,
        "listings": listings,
    }

    # Add note about distance if no zip_code provided
    if not zip_code:
        result["note"] = "Provide zip_code parameter (e.g., '1016LV') to enable distance filtering and see distances"

    # Add pagination hint
    if offset + len(listings) < total_count:
        result["next_offset"] = offset + len(listings)

    return result


@mcp.tool()
def get_listing_details(listing_id: str) -> dict[str, Any]:
    """
    Get full details of a specific listing including complete description and all images.

    Args:
        listing_id: The listing ID (e.g., "m2340580395")

    Returns:
        Full listing details including description, images, attributes, and seller info
    """
    if not listing_id:
        return {"error": "Please provide a listing_id"}

    # Ensure ID has the 'm' prefix
    if not listing_id.startswith("m"):
        listing_id = f"m{listing_id}"

    try:
        # Fetch the listing page
        response = _get_request(f"{LISTING_URL}/{listing_id}", headers=HTML_HEADERS)
        response.raise_for_status()

        if response.status_code == 404 or "niet gevonden" in response.text.lower():
            return {"error": "Listing not found"}

        soup = BeautifulSoup(response.text, "html.parser")

        result = {
            "id": listing_id,
            "url": response.url,
        }

        # Extract JSON-LD data
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string)
                if isinstance(data, dict) and data.get("@type") == "Product":
                    result["title"] = data.get("name")
                    result["description_short"] = data.get("description")

                    offers = data.get("offers", {})
                    result["price"] = f"€ {offers.get('price', 0)}"
                    result["price_cents"] = int(float(offers.get("price", 0)) * 100)
                    result["availability"] = "In Stock" if "InStock" in offers.get("availability", "") else "Unknown"

                    # Images
                    images = data.get("image", [])
                    result["images"] = [
                        ("https:" + img if not img.startswith("http") else img)
                        for img in images
                    ]
                    result["image_count"] = len(images)
            except (json.JSONDecodeError, TypeError):
                pass

        # Extract full description from page text
        text = soup.get_text(separator="|||")
        if "Beschrijving" in text:
            parts = text.split("|||")
            in_description = False
            description_lines = []

            for part in parts:
                part = part.strip()
                if not part:
                    continue
                if part == "Beschrijving":
                    in_description = True
                    continue
                if in_description:
                    if part in ["Kenmerken", "Locatie", "Bied nu", "Bericht", "Vragen aan verkoper"]:
                        break
                    description_lines.append(part)

            if description_lines:
                result["description_full"] = " ".join(description_lines)

                # Extract specs from full description
                specs = _extract_specs_from_description(
                    result["description_full"],
                    result.get("title", "")
                )
                if specs:
                    result["specs"] = specs

        # Extract attributes/kenmerken
        attributes = {}
        attr_patterns = [
            (r"Conditie\s*(\w+)", "conditie"),
            (r"Merk\s*(\w+)", "merk"),
            (r"Framehoogte\s*([\d\s\-totcm]+)", "framehoogte"),
            (r"Schermgrootte\s*([\d\s\-inch]+)", "schermgrootte"),
            (r"Werkgeheugen[^\d]*([\d]+\s*GB)", "werkgeheugen"),
            (r"Processorsnelheid[^\d]*([\d,\.]+\s*GHz)", "processorsnelheid"),
            (r"Type opslag\s*(\w+)", "type_opslag"),
        ]

        for pattern, key in attr_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                attributes[key] = match.group(1).strip()

        if attributes:
            result["attributes"] = attributes

        # Extract statistics
        views_match = re.search(r"([\d.]+)x bekeken", text)
        saved_match = re.search(r"(\d+)x bewaard", text)
        date_match = re.search(r"Sinds (\d+ \w+ '\d+)", text)

        stats = {}
        if views_match:
            stats["views"] = views_match.group(1)
        if saved_match:
            stats["saved"] = int(saved_match.group(1))
        if date_match:
            stats["online_since"] = date_match.group(1)

        if stats:
            result["statistics"] = stats

        # Extract location
        location_match = re.search(r"Locatie[^\w]*(\w[\w\s]+?)(?:[\d.]+x bekeken|Toon|Op de kaart)", text)
        if location_match:
            result["location"] = location_match.group(1).strip()

        return result

    except requests.RequestException as e:
        return {"error": f"Request failed: {str(e)}"}


@mcp.tool()
def get_seller_info(seller_id: int) -> dict[str, Any]:
    """
    Get detailed information about a seller including ratings and verification status.

    Args:
        seller_id: The seller's numeric ID

    Returns:
        Seller profile with ratings, verification status, and statistics
    """
    if not seller_id:
        return {"error": "Please provide a seller_id"}

    try:
        response = _get_request(f"{SELLER_URL}/{seller_id}")
        response.raise_for_status()
        data = response.json()

        return {
            "id": data.get("sellerId"),
            "name": data.get("sellerName"),
            "is_verified": data.get("isVerified", False),
            "average_score": data.get("averageScore"),
            "number_of_reviews": data.get("numberOfReviews", 0),
            "verification": {
                "bank_account": data.get("bankAccountVerified", False),
                "identification": data.get("identificationVerified", False),
                "phone_number": data.get("phoneNumberVerified", False),
            },
        }

    except requests.RequestException as e:
        return {"error": f"Request failed: {str(e)}"}
    except json.JSONDecodeError:
        return {"error": "Invalid response from Marktplaats"}


@mcp.tool()
def list_categories() -> dict[str, Any]:
    """
    List all available main categories and common subcategories on Marktplaats.

    Returns:
        Dictionary with main categories and subcategories
    """
    main_categories = [
        {"name": name.title(), "id": id_}
        for name, id_ in sorted(L1_CATEGORIES.items())
    ]

    subcategories = [
        {"name": name.title(), "id": info["id"], "parent_id": info["parent"]}
        for name, info in sorted(L2_CATEGORIES.items())
    ]

    return {
        "main_categories": main_categories,
        "subcategories": subcategories,
        "note": "Use category names (not IDs) in search_listings. For more subcategories, search with a main category first.",
    }


@mcp.tool()
def get_category_filters(
    category: str | None = None,
    subcategory: str | None = None,
) -> dict[str, Any]:
    """
    Get available filter options for a specific category (like RAM, brand, screen size, etc.).

    Args:
        category: Main category name (e.g., "computers en software")
        subcategory: Subcategory name (e.g., "laptops")

    Returns:
        Available filters with their IDs that can be used in search_listings
    """
    if not category and not subcategory:
        return {"error": "Please provide a category or subcategory"}

    params: dict[str, Any] = {
        "limit": "1",
        "query": "",
    }

    if subcategory:
        subcat_lower = subcategory.lower()
        if subcat_lower in L2_CATEGORIES:
            params["l2CategoryId"] = str(L2_CATEGORIES[subcat_lower]["id"])
            params["l1CategoryId"] = str(L2_CATEGORIES[subcat_lower]["parent"])
        else:
            return {"error": f"Unknown subcategory: {subcategory}"}
    elif category:
        cat_lower = category.lower()
        if cat_lower in L1_CATEGORIES:
            params["l1CategoryId"] = str(L1_CATEGORIES[cat_lower])
        else:
            return {"error": f"Unknown category: {category}"}

    try:
        response = _get_request(SEARCH_URL, params)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        return {"error": f"Request failed: {str(e)}"}
    except json.JSONDecodeError:
        return {"error": "Invalid response from Marktplaats"}

    filters = {}
    skip_keys = {"PriceCents", "RelevantCategories", "offeredSince"}

    for facet in data.get("facets", []):
        key = facet.get("key")
        label = facet.get("label", key)

        if key in skip_keys:
            continue

        if "attributeGroup" in facet and facet["attributeGroup"]:
            options = []
            for attr in facet["attributeGroup"]:
                attr_id = attr.get("attributeValueId")
                if attr_id is not None:  # Skip options without ID (like date filters)
                    options.append({
                        "name": attr.get("attributeValueLabel", attr.get("attributeValueKey")),
                        "id": attr_id,
                        "count": attr.get("histogramCount", 0),
                    })

            if options:
                filters[label] = options

    return {
        "category": subcategory or category,
        "filters": filters,
        "usage": "Use the 'id' values in the 'attribute_ids' parameter of search_listings",
    }


def main():
    """Run the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
