#!/usr/bin/env python3
"""Web application health check script.

Checks: HTTP status, response time, SSL certificate, security headers,
broken links (first 20), page size. Outputs JSON result.

Usage:
    python3 health_check.py <URL>
    python health_check.py <URL>   (Windows)
"""

import json
import ssl
import socket
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
import re
from html.parser import HTMLParser


class LinkExtractor(HTMLParser):
    """Extract href links from HTML."""

    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            for attr_name, attr_value in attrs:
                if attr_name == "href" and attr_value:
                    self.links.append(attr_value)


def check_ssl_certificate(hostname, port=443):
    """Check SSL certificate validity and expiry."""
    result = {
        "valid": False,
        "expiry_date": None,
        "days_remaining": None,
        "issuer": None,
        "error": None,
    }
    try:
        context = ssl.create_default_context()
        with socket.create_connection((hostname, port), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert = ssock.getpeercert()
                result["valid"] = True
                # Parse expiry date
                expire_str = cert.get("notAfter", "")
                if expire_str:
                    try:
                        expire_date = ssl.cert_time_to_seconds(expire_str)
                        import datetime
                        expire_dt = datetime.datetime.utcfromtimestamp(expire_date)
                        now = datetime.datetime.utcnow()
                        result["expiry_date"] = expire_dt.isoformat() + "Z"
                        result["days_remaining"] = (expire_dt - now).days
                    except Exception:
                        pass
                # Get issuer
                issuer_parts = cert.get("issuer", ())
                for part in issuer_parts:
                    for key, value in part:
                        if key == "organizationName":
                            result["issuer"] = value
                            break
                    if result["issuer"]:
                        break
    except ssl.SSLCertVerificationError as e:
        result["error"] = f"Certificate verification failed: {e.verify_message}"
    except ssl.SSLError as e:
        result["error"] = f"SSL error: {e}"
    except socket.timeout:
        result["error"] = "Connection timed out"
    except ConnectionRefusedError:
        result["error"] = "Connection refused"
    except Exception as e:
        result["error"] = str(e)
    return result


def check_security_headers(headers):
    """Check security-related HTTP headers."""
    security_headers = {
        "X-Frame-Options": headers.get("X-Frame-Options"),
        "Content-Security-Policy": headers.get("Content-Security-Policy"),
        "Strict-Transport-Security": headers.get("Strict-Transport-Security"),
        "X-Content-Type-Options": headers.get("X-Content-Type-Options"),
    }
    result = {}
    for header_name, value in security_headers.items():
        result[header_name] = {
            "present": value is not None,
            "value": value if value is not None else None,
        }
    return result


def check_url(url, timeout=10):
    """Check a single URL and return status info."""
    result = {
        "url": url,
        "status_code": None,
        "response_time_ms": None,
        "error": None,
    }
    try:
        start = time.time()
        req = urllib.request.Request(url, method="HEAD", headers={
            "User-Agent": "Mozilla/5.0 (compatible; WebAppHealthCheck/1.0)"
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result["status_code"] = resp.status
        elapsed = (time.time() - start) * 1000
        result["response_time_ms"] = round(elapsed, 1)
    except urllib.error.HTTPError as e:
        result["status_code"] = e.code
        result["error"] = str(e)
    except urllib.error.URLError as e:
        result["error"] = str(e.reason)
    except Exception as e:
        result["error"] = str(e)
    return result


def extract_links(html_content, base_url):
    """Extract and resolve links from HTML content."""
    extractor = LinkExtractor()
    try:
        extractor.feed(html_content)
    except Exception:
        pass

    parsed_base = urllib.parse.urlparse(base_url)
    resolved_links = []
    seen = set()

    for link in extractor.links:
        # Skip anchors, javascript, mailto
        if link.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        # Resolve relative URLs
        resolved = urllib.parse.urljoin(base_url, link)
        # Only check http/https links
        parsed = urllib.parse.urlparse(resolved)
        if parsed.scheme not in ("http", "https"):
            continue
        # Normalize: remove fragment
        normalized = urllib.parse.urlunparse(parsed._replace(fragment=""))
        if normalized not in seen:
            seen.add(normalized)
            resolved_links.append(normalized)

    return resolved_links


def format_size(size_bytes):
    """Format bytes to human-readable size."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 health_check.py <URL>", file=sys.stderr)
        sys.exit(1)

    url = sys.argv[1]
    parsed = urllib.parse.urlparse(url)

    if parsed.scheme not in ("http", "https"):
        print(json.dumps({"error": "URL must start with http:// or https://"}, indent=2))
        sys.exit(1)

    result = {
        "url": url,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "http_status": None,
        "response_time_ms": None,
        "page_size": None,
        "ssl": None,
        "security_headers": None,
        "links": None,
        "error": None,
    }

    # 1. HTTP status + response time + page content
    html_content = ""
    try:
        start = time.time()
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; WebAppHealthCheck/1.0)"
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            result["http_status"] = resp.status
            html_content = resp.read().decode("utf-8", errors="replace")
        elapsed = (time.time() - start) * 1000
        result["response_time_ms"] = round(elapsed, 1)
        result["page_size"] = len(html_content.encode("utf-8"))
        result["page_size_human"] = format_size(result["page_size"])
        headers = dict(resp.headers)
    except urllib.error.HTTPError as e:
        result["http_status"] = e.code
        result["error"] = f"HTTP Error: {e.code} {e.reason}"
        headers = dict(e.headers)
    except urllib.error.URLError as e:
        result["error"] = f"URL Error: {e.reason}"
        headers = {}
    except Exception as e:
        result["error"] = str(e)
        headers = {}

    # 2. SSL certificate check (only for HTTPS)
    if parsed.scheme == "https":
        result["ssl"] = check_ssl_certificate(parsed.hostname, parsed.port or 443)

    # 3. Security headers
    result["security_headers"] = check_security_headers(headers)

    # 4. Link checking (only if we got HTML content)
    if html_content:
        links = extract_links(html_content, url)
        links_to_check = links[:20]
        link_results = []
        for link_url in links_to_check:
            link_result = check_url(link_url)
            link_results.append(link_result)
        result["links"] = {
            "total_found": len(links),
            "checked": len(links_to_check),
            "results": link_results,
            "broken": [
                {"url": r["url"], "status_code": r["status_code"], "error": r["error"]}
                for r in link_results
                if r["status_code"] is not None and r["status_code"] >= 400
                or r["error"] is not None and r["status_code"] is None
            ],
        }
    else:
        result["links"] = {
            "total_found": 0,
            "checked": 0,
            "results": [],
            "broken": [],
        }

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
