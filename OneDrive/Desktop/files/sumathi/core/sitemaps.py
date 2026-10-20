from django.contrib.sitemaps import Sitemap
from django.urls import reverse

class StaticSitemap(Sitemap):
    priority, changefreq = 0.8, "monthly"
    def items(self): return ["home", "about", "gallery", "contact"]
    def location(self, item): return reverse(item)
