import requests
from bs4 import BeautifulSoup
import re
import os

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

def scrape_indeed(url: str) -> dict:
    """Scrape job posting from Indeed."""
    try:
        headers = {"User-Agent": USER_AGENT}
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Try to find Indeed's structured data
        script_tag = soup.find("script", {"id": "indeed_atstk_data"})
        if script_tag:
            import json
            data = json.loads(script_tag.string)
            return {
                "title": data.get("title", ""),
                "company": data.get("company", ""),
                "location": data.get("location", ""),
                "description": data.get("description", ""),
                "url": url
            }
        
        # Fallback: extract from HTML
        title = ""
        title_tag = soup.find("h1", {"class": "jobsearch-JobInfoHeader-title"})
        if title_tag:
            title = title_tag.get_text(strip=True)
        
        company = ""
        company_tag = soup.find("div", {"class": "jobsearch-CompanyInfoWithoutHeaderImage"})
        if company_tag:
            company = company_tag.get_text(strip=True)
        
        location = ""
        loc_tag = soup.find("div", {"id": "searchLocationBox"})
        if loc_tag:
            location = loc_tag.get_text(strip=True)
        
        desc = ""
        desc_tag = soup.find("div", {"id": "jobDescriptionText"})
        if desc_tag:
            desc = desc_tag.get_text(separator="\n", strip=True)
        
        return {
            "title": title,
            "company": company,
            "location": location,
            "description": desc,
            "url": url
        }
    except Exception as e:
        return {"error": f"Failed to scrape Indeed: {str(e)}", "url": url}


def scrape_generic(url: str) -> dict:
    """Scrape job posting from any URL."""
    try:
        headers = {"User-Agent": USER_AGENT}
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Remove script and style elements
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        
        # Try to find title
        title = ""
        title_tag = soup.find("h1")
        if title_tag:
            title = title_tag.get_text(strip=True)
        
        # Try to find description (common selectors)
        description = ""
        desc_candidates = [
            soup.find("div", {"class": re.compile(r"description|job-description|jobdescription", re.I)}),
            soup.find("section", {"id": re.compile(r"description|job|content", re.I)}),
            soup.find("div", {"class": re.compile(r"content|main|description", re.I)}),
        ]
        for candidate in desc_candidates:
            if candidate:
                text = candidate.get_text(separator="\n", strip=True)
                if len(text) > 200:
                    description = text
                    break
        
        if not description:
            # Fallback: get all paragraph text
            paragraphs = soup.find_all("p")
            description = "\n".join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 50)
        
        # Try to find company name
        company = ""
        company_tag = soup.find("a", {"class": re.compile(r"company|employer", re.I)})
        if company_tag:
            company = company_tag.get_text(strip=True)
        
        # Try to find location
        location = ""
        loc_tag = soup.find({"class": re.compile(r"location|location", re.I)})
        if loc_tag:
            location = loc_tag.get_text(strip=True)
        
        return {
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "url": url
        }
    except Exception as e:
        return {"error": f"Failed to scrape URL: {str(e)}", "url": url}


def scrape_job_url(url: str) -> dict:
    """Scrape job posting from URL. Auto-detect platform."""
    if "indeed" in url.lower():
        return scrape_indeed(url)
    else:
        return scrape_generic(url)


def parse_job_text(text: str) -> dict:
    """Parse pasted job description text directly."""
    lines = text.split("\n")
    title = ""
    description = text
    
    # First non-empty line often is the title
    for line in lines:
        if line.strip():
            title = line.strip()
            break
    
    return {
        "title": title,
        "company": "",
        "location": "",
        "description": text,
        "url": ""
    }