"""News search links for a member + company. Automatic matching proved unreliable (Google News RSS
ignores quoted phrases), so the app links to the search instead of scoring press coverage."""
import urllib.parse


def news_url(member_name, company_key):
    return "https://news.google.com/search?q=" + urllib.parse.quote(f'"{member_name}" "{company_key}"')
