#!/usr/bin/env python3
"""Web application security scanner script.

Checks: SSL/TLS certificate, security headers, information disclosure,
mixed content, cookie flags. Outputs JSON result with severity levels.

Usage:
    python3 scan_security.py <URL>
    python scan_security.py <URL>   (Windows)
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


class ResourceExtractor(HTMLParser):
    """Extract resource URLs from HTML (scripts, images, links, etc.)."""

    RESOURCE_ATTRS = {
        "script": ["src"],
        "img": ["src"],
        "link": ["href"],
        "iframe": ["src"],
        "video": ["src", "poster"],
        "audio": ["src"],
        "source": ["src"],
        "embed": ["src"],
        "object": ["data"],
    }

    def __init__(self):
        super().__init__()
        self.resources = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        tag_lower = tag.lower()
        attr_names = self.RESOURCE_ATTRS.get(tag_lower, [])
        for attr_name in attr_names:
            value = attrs_dict.get(attr_name)
            if value:
                self.resources.append(value)
        # Also check inline style for url() references
        style = attrs_dict.get("style", "")
        if style:
            urls = re.findall(r'url\(["\']?(http://[^)"\']+)["\']?\)', style, re.IGNORECASE)
            self.resources.extend(urls)


def check_ssl_certificate(hostname, port=443):
    """Check SSL/TLS certificate validity and expiry."""
    findings = []
    result = {
        "valid": False,
        "expiry_date": None,
        "days_remaining": None,
        "issuer": None,
        "protocol": None,
        "findings": findings,
    }
    try:
        context = ssl.create_default_context()
        with socket.create_connection((hostname, port), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert = ssock.getpeercert()
                result["valid"] = True
                result["protocol"] = ssock.version()

                # Parse expiry date
                expire_str = cert.get("notAfter", "")
                if expire_str:
                    try:
                        expire_date = ssl.cert_time_to_seconds(expire_str)
                        import datetime
                        expire_dt = datetime.datetime.utcfromtimestamp(expire_date)
                        now = datetime.datetime.utcnow()
                        result["expiry_date"] = expire_dt.isoformat() + "Z"
                        days_remaining = (expire_dt - now).days
                        result["days_remaining"] = days_remaining

                        if days_remaining < 0:
                            findings.append({
                                "severity": "critical",
                                "message": f"SSL certificate expired {abs(days_remaining)} days ago",
                            })
                        elif days_remaining <= 7:
                            findings.append({
                                "severity": "critical",
                                "message": f"SSL certificate expires in {days_remaining} days",
                            })
                        elif days_remaining <= 30:
                            findings.append({
                                "severity": "warning",
                                "message": f"SSL certificate expires in {days_remaining} days",
                            })
                        else:
                            findings.append({
                                "severity": "info",
                                "message": f"SSL certificate valid, expires in {days_remaining} days",
                            })
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
        result["valid"] = False
        findings.append({
            "severity": "critical",
            "message": f"SSL certificate verification failed: {e.verify_message}",
        })
    except ssl.SSLError as e:
        result["valid"] = False
        findings.append({
            "severity": "critical",
            "message": f"SSL error: {e}",
        })
    except socket.timeout:
        findings.append({
            "severity": "critical",
            "message": "SSL connection timed out",
        })
    except ConnectionRefusedError:
        findings.append({
            "severity": "critical",
            "message": "SSL connection refused",
        })
    except Exception as e:
        findings.append({
            "severity": "warning",
            "message": f"Could not check SSL: {e}",
        })
    return result


def check_security_headers(headers, is_https):
    """Check security-related HTTP headers."""
    checks = [
        {
            "header": "Content-Security-Policy",
            "severity_if_missing": "warning",
            "description": "Prevents XSS and data injection attacks by controlling resources the browser can load",
        },
        {
            "header": "Strict-Transport-Security",
            "severity_if_missing": "critical" if is_https else "info",
            "description": "Forces browsers to only use HTTPS, preventing protocol downgrade and cookie hijacking",
        },
        {
            "header": "X-Frame-Options",
            "severity_if_missing": "warning",
            "description": "Prevents clickjacking by controlling whether the page can be embedded in frames",
        },
        {
            "header": "X-Content-Type-Options",
            "severity_if_missing": "warning",
            "description": "Prevents MIME-type sniffing, reduces risk of drive-by downloads",
        },
        {
            "header": "Referrer-Policy",
            "severity_if_missing": "info",
            "description": "Controls how much referrer information is shared when navigating away",
        },
        {
            "header": "Permissions-Policy",
            "severity_if_missing": "info",
            "description": "Controls which browser features and APIs can be used in the browser",
        },
    ]

    result = []
    for check in checks:
        header_name = check["header"]
        value = headers.get(header_name)
        if value is not None:
            result.append({
                "header": header_name,
                "present": True,
                "value": value,
                "severity": "info",
                "message": f"{header_name} is configured",
            })
        else:
            result.append({
                "header": header_name,
                "present": False,
                "value": None,
                "severity": check["severity_if_missing"],
                "message": f"{header_name} is missing. {check['description']}",
            })
    return result


def check_information_disclosure(headers):
    """Check for information disclosure in HTTP headers."""
    findings = []

    # Server header
    server = headers.get("Server")
    if server:
        findings.append({
            "severity": "warning",
            "type": "server_header",
            "message": f"Server header exposes: '{server}'. Consider removing or obscuring.",
            "value": server,
        })

    # X-Powered-By header
    x_powered = headers.get("X-Powered-By")
    if x_powered:
        findings.append({
            "severity": "warning",
            "type": "x_powered_by",
            "message": f"X-Powered-By header exposes: '{x_powered}'. Consider removing.",
            "value": x_powered,
        })

    # X-AspNet-Version header
    aspnet_ver = headers.get("X-AspNet-Version")
    if aspnet_ver:
        findings.append({
            "severity": "warning",
            "type": "aspnet_version",
            "message": f"X-AspNet-Version header exposes: '{aspnet_ver}'. Consider removing.",
            "value": aspnet_ver,
        })

    if not findings:
        findings.append({
            "severity": "info",
            "type": "info_disclosure",
            "message": "No information disclosure detected in HTTP headers",
            "value": None,
        })

    return findings


def check_mixed_content(html_content, base_url):
    """Check for mixed content (HTTP resources on HTTPS page)."""
    findings = []
    http_resources = []

    extractor = ResourceExtractor()
    try:
        extractor.feed(html_content)
    except Exception:
        pass

    parsed_base = urllib.parse.urlparse(base_url)
    if parsed_base.scheme != "https":
        findings.append({
            "severity": "info",
            "message": "Page is served over HTTP; mixed content check is only relevant for HTTPS pages",
            "http_resources": [],
        })
        return findings

    for resource_url in extractor.resources:
        # Resolve relative URLs
        resolved = urllib.parse.urljoin(base_url, resource_url)
        parsed = urllib.parse.urlparse(resolved)
        if parsed.scheme == "http":
            http_resources.append(resolved)

    if http_resources:
        findings.append({
            "severity": "warning",
            "message": f"Found {len(http_resources)} HTTP resource(s) on HTTPS page (mixed content)",
            "http_resources": http_resources[:20],  # Limit to first 20
        })
    else:
        findings.append({
            "severity": "info",
            "message": "No mixed content detected",
            "http_resources": [],
        })

    return findings


def check_cookie_flags(headers):
    """Check cookie security flags from Set-Cookie headers."""
    findings = []

    # Get all Set-Cookie headers (there can be multiple)
    set_cookie_headers = headers.get_all("Set-Cookie") if hasattr(headers, "get_all") else []
    if not set_cookie_headers:
        # Try single header
        single = headers.get("Set-Cookie")
        if single:
            set_cookie_headers = [single]

    if not set_cookie_headers:
        findings.append({
            "severity": "info",
            "message": "No cookies set by this response",
            "cookies": [],
        })
        return findings

    cookies = []
    for cookie_str in set_cookie_headers:
        # Parse cookie name and flags
        parts = cookie_str.split(";")
        name_value = parts[0].strip()
        cookie_name = name_value.split("=")[0].strip() if "=" in name_value else name_value

        cookie_str_lower = cookie_str.lower()
        has_secure = "secure" in cookie_str_lower
        has_httponly = "httponly" in cookie_str_lower
        has_samesite = "samesite" in cookie_str_lower

        cookie_info = {
            "name": cookie_name,
            "secure": has_secure,
            "httponly": has_httponly,
            "samesite": has_samesite,
        }
        cookies.append(cookie_info)

        # Flag missing security attributes
        if not has_secure:
            findings.append({
                "severity": "warning",
                "message": f"Cookie '{cookie_name}' missing Secure flag. Cookie can be sent over HTTP, vulnerable to interception.",
                "cookie": cookie_name,
                "flag": "Secure",
            })
        if not has_httponly:
            findings.append({
                "severity": "warning",
                "message": f"Cookie '{cookie_name}' missing HttpOnly flag. Cookie accessible via JavaScript, vulnerable to XSS.",
                "cookie": cookie_name,
                "flag": "HttpOnly",
            })
        if not has_samesite:
            findings.append({
                "severity": "info",
                "message": f"Cookie '{cookie_name}' missing SameSite flag. Cookie may be sent with cross-site requests, vulnerable to CSRF.",
                "cookie": cookie_name,
                "flag": "SameSite",
            })

    if not findings:
        findings.append({
            "severity": "info",
            "message": "All cookies have proper security flags",
            "cookies": cookies,
        })

    return findings


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scan_security.py <URL>", file=sys.stderr)
        sys.exit(1)

    url = sys.argv[1]
    parsed = urllib.parse.urlparse(url)

    if parsed.scheme not in ("http", "https"):
        print(json.dumps({"error": "URL must start with http:// or https://"}, indent=2))
        sys.exit(1)

    is_https = parsed.scheme == "https"

    result = {
        "url": url,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ssl_tls": None,
        "security_headers": None,
        "information_disclosure": None,
        "mixed_content": None,
        "cookie_flags": None,
        "summary": {
            "critical": 0,
            "warning": 0,
            "info": 0,
        },
        "error": None,
    }

    # Fetch the page
    html_content = ""
    response_headers = {}
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; SecurityScanner/1.0)"
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            html_content = resp.read().decode("utf-8", errors="replace")
            response_headers = resp.headers
    except urllib.error.HTTPError as e:
        response_headers = e.headers
        result["error"] = f"HTTP Error: {e.code} {e.reason}"
    except urllib.error.URLError as e:
        result["error"] = f"URL Error: {e.reason}"
        print(json.dumps(result, indent=2, ensure_ascii=False))
        sys.exit(1)
    except Exception as e:
        result["error"] = str(e)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        sys.exit(1)

    # 1. SSL/TLS check
    if is_https:
        result["ssl_tls"] = check_ssl_certificate(parsed.hostname, parsed.port or 443)

    # 2. Security headers
    result["security_headers"] = check_security_headers(response_headers, is_https)

    # 3. Information disclosure
    result["information_disclosure"] = check_information_disclosure(response_headers)

    # 4. Mixed content
    result["mixed_content"] = check_mixed_content(html_content, url)

    # 5. Cookie flags
    result["cookie_flags"] = check_cookie_flags(response_headers)

    # Count severities
    all_findings = []
    if result["ssl_tls"]:
        all_findings.extend(result["ssl_tls"].get("findings", []))
    all_findings.extend(result["security_headers"])
    all_findings.extend(result["information_disclosure"])
    for mc in result["mixed_content"]:
        all_findings.append(mc)
    all_findings.extend(result["cookie_flags"])

    for finding in all_findings:
        severity = finding.get("severity", "info")
        if severity in result["summary"]:
            result["summary"][severity] += 1

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
