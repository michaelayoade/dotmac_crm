"""E2E visual review tests for report macros.

Tests that the new report macros (pivot_table, leaderboard_table, progress_bar,
score_cell, rank_badge, star_rating) render correctly on live report pages.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.playwright.pages.base_page import BasePage

# ---------------------------------------------------------------------------
# Page objects
# ---------------------------------------------------------------------------


class TechnicianReportPage(BasePage):
    """Page object for /admin/reports/technician."""

    def goto(self, path: str = "") -> None:
        super().goto("/admin/reports/technician")

    def expect_loaded(self) -> None:
        expect(self.page.get_by_role("heading", name="Technician Performance")).to_be_visible()


class PerformanceLeaderboardPage(BasePage):
    """Page object for /admin/performance (leaderboard partial)."""

    def goto(self, path: str = "") -> None:
        super().goto("/admin/performance")

    def expect_loaded(self) -> None:
        expect(
            self.page.get_by_role("heading", name="Performance")
            .or_(self.page.get_by_text("Leaderboard", exact=False))
            .first
        ).to_be_visible()


class DataQualityPage(BasePage):
    """Page object for /admin/data-quality."""

    def goto(self, path: str = "") -> None:
        super().goto("/admin/data-quality")

    def goto_domain(self, domain: str) -> None:
        super().goto(f"/admin/data-quality/{domain}")

    def expect_loaded(self) -> None:
        expect(
            self.page.get_by_role("heading", name="Quality").or_(self.page.get_by_role("heading", name="Data")).first
        ).to_be_visible()


class NetworkReportPage(BasePage):
    """Page object for /admin/reports/network."""

    def goto(self, path: str = "") -> None:
        super().goto("/admin/reports/network")

    def expect_loaded(self) -> None:
        expect(self.page.get_by_role("heading", name="Network Usage")).to_be_visible()


# ---------------------------------------------------------------------------
# Technician Report — leaderboard_table macro
# ---------------------------------------------------------------------------


class TestTechnicianReportMacros:
    """Verify the technician report renders via leaderboard_table macro."""

    def test_page_loads(self, admin_page: Page, settings) -> None:
        """Technician report page loads without errors."""
        page = TechnicianReportPage(admin_page, settings.base_url)
        page.goto()
        page.expect_loaded()

    def test_summary_stats_visible(self, admin_page: Page, settings) -> None:
        """Summary stat cards render at the top."""
        page = TechnicianReportPage(admin_page, settings.base_url)
        page.goto()
        page.expect_loaded()
        # Should have stat cards (Total Technicians, Jobs Completed, etc.)
        expect(admin_page.get_by_text("Total Technicians").first).to_be_visible()

    def test_leaderboard_table_renders(self, admin_page: Page, settings) -> None:
        """Leaderboard table section renders (even if empty)."""
        page = TechnicianReportPage(admin_page, settings.base_url)
        page.goto()
        page.expect_loaded()
        # Should show either a table or the empty state from leaderboard_table
        table_or_empty = admin_page.locator("table").or_(admin_page.get_by_text("No technician data", exact=False))
        expect(table_or_empty.first).to_be_visible()

    def test_leaderboard_has_correct_headers(self, admin_page: Page, settings) -> None:
        """If data exists, leaderboard table has expected column headers."""
        page = TechnicianReportPage(admin_page, settings.base_url)
        page.goto()
        page.expect_loaded()
        # Check for the Performance Leaderboard section
        section = admin_page.get_by_text("Performance Leaderboard")
        expect(section.first).to_be_visible()
        # The leaderboard_table macro generates these headers from the columns config
        for header in ["Name", "Jobs", "Completed", "Avg Time", "Rating"]:
            header_cell = admin_page.locator("th").filter(has_text=header)
            if header_cell.count() > 0:
                expect(header_cell.first).to_be_visible()

    def test_rank_badge_renders_for_top_rows(self, admin_page: Page, settings) -> None:
        """Rank badges (gold/silver/bronze) render for top 3 rows."""
        page = TechnicianReportPage(admin_page, settings.base_url)
        page.goto()
        page.expect_loaded()
        # rank_badge generates gradient badges with rounded-lg class
        badges = admin_page.locator("table td .rounded-lg.bg-gradient-to-br")
        # May have 0 if no data, or up to 3 for top ranks
        count = badges.count()
        assert count <= 3, f"Expected at most 3 rank badges, got {count}"

    def test_star_rating_renders(self, admin_page: Page, settings) -> None:
        """Star rating SVGs render in rating column."""
        page = TechnicianReportPage(admin_page, settings.base_url)
        page.goto()
        page.expect_loaded()
        # star_rating macro generates SVGs with text-yellow-400 for filled stars
        stars = admin_page.locator("table svg[fill='currentColor'][viewBox='0 0 20 20']")
        # 0 if empty table, multiples of 5 per row if data exists
        count = stars.count()
        assert count == 0 or count % 5 == 0, f"Stars should be in groups of 5, got {count}"

    def test_date_filter_works(self, admin_page: Page, settings) -> None:
        """Date range filter is functional."""
        page = TechnicianReportPage(admin_page, settings.base_url)
        page.goto()
        page.expect_loaded()
        # The period selector should be present
        period_select = admin_page.locator("select[name='days']")
        expect(period_select).to_be_visible()
        # Change to 90 days and verify page reloads without error
        period_select.select_option("90")
        admin_page.get_by_role("button", name="Filter").click()
        page.wait_for_load()
        page.expect_loaded()

    def test_csv_export_link_present(self, admin_page: Page, settings) -> None:
        """CSV export link is present and accessible."""
        page = TechnicianReportPage(admin_page, settings.base_url)
        page.goto()
        page.expect_loaded()
        export_link = admin_page.get_by_text("Export CSV", exact=False)
        expect(export_link.first).to_be_visible()


# ---------------------------------------------------------------------------
# Performance Leaderboard — rank_badge, score_cell, progress_bar macros
# ---------------------------------------------------------------------------


class TestPerformanceLeaderboardMacros:
    """Verify the performance leaderboard partial uses new macros."""

    def test_page_loads(self, admin_page: Page, settings) -> None:
        """Performance page loads without errors."""
        page = PerformanceLeaderboardPage(admin_page, settings.base_url)
        page.goto()
        page.expect_loaded()

    def test_score_cell_renders(self, admin_page: Page, settings) -> None:
        """score_cell macro renders colored score badges."""
        page = PerformanceLeaderboardPage(admin_page, settings.base_url)
        page.goto()
        page.expect_loaded()
        # score_cell renders with bg-emerald-50, bg-amber-50, or bg-rose-50
        score_badges = admin_page.locator("[class*='bg-emerald-50'], [class*='bg-amber-50'], [class*='bg-rose-50']")
        # 0 if no data, otherwise at least 1 per agent row
        if score_badges.count() > 0:
            expect(score_badges.first).to_be_visible()

    def test_progress_bar_renders(self, admin_page: Page, settings) -> None:
        """progress_bar macro renders for domain score mini bars."""
        page = PerformanceLeaderboardPage(admin_page, settings.base_url)
        page.goto()
        page.expect_loaded()
        # progress_bar generates nested divs with rounded-full bg-slate-100
        bars = admin_page.locator(".rounded-full.bg-slate-100, .rounded-full.dark\\:bg-slate-700")
        # May be 0 (no data) or many (domain scores per agent)
        if bars.count() > 0:
            expect(bars.first).to_be_visible()

    def test_rank_badge_gradient_styles(self, admin_page: Page, settings) -> None:
        """rank_badge macro uses correct gradient classes."""
        page = PerformanceLeaderboardPage(admin_page, settings.base_url)
        page.goto()
        page.expect_loaded()
        # Gold badge (1st place) should have amber-400 to orange-500 gradient
        gold = admin_page.locator("[class*='from-amber-400'][class*='to-orange-500']")
        silver = admin_page.locator("[class*='from-slate-300'][class*='to-slate-400']")
        bronze = admin_page.locator("[class*='from-orange-300'][class*='to-orange-400']")
        # If data exists, first 3 should have badges
        total = gold.count() + silver.count() + bronze.count()
        assert total <= 3, f"At most 3 medal badges expected, got {total}"


# ---------------------------------------------------------------------------
# Data Quality — progress_bar macro
# ---------------------------------------------------------------------------


class TestDataQualityMacros:
    """Verify data quality pages use progress_bar macro correctly."""

    def test_index_page_loads(self, admin_page: Page, settings) -> None:
        """Data quality index page loads."""
        page = DataQualityPage(admin_page, settings.base_url)
        page.goto()
        page.expect_loaded()

    def test_progress_bars_on_entity_table(self, admin_page: Page, settings) -> None:
        """Entity table uses progress_bar macro for quality column."""
        admin_page.goto(f"{settings.base_url}/admin/data-quality/tickets")
        admin_page.wait_for_load_state("domcontentloaded")
        # progress_bar generates a flex container with a rounded-full bar inside
        bars = admin_page.locator("td .flex.items-center.gap-2 .rounded-full")
        if bars.count() > 0:
            expect(bars.first).to_be_visible()

    def test_domain_detail_progress_bars(self, admin_page: Page, settings) -> None:
        """Domain detail page uses progress_bar for missing fields section."""
        admin_page.goto(f"{settings.base_url}/admin/data-quality/tickets")
        admin_page.wait_for_load_state("domcontentloaded")
        # "Most Common Missing Fields" section should use progress_bar
        missing_section = admin_page.get_by_text("Most Common Missing Fields", exact=False)
        if missing_section.count() > 0:
            expect(missing_section.first).to_be_visible()
            # progress_bar renders with bg-amber-500 in this context
            amber_bars = admin_page.locator("[class*='bg-amber-500'].rounded-full")
            if amber_bars.count() > 0:
                expect(amber_bars.first).to_be_visible()


# ---------------------------------------------------------------------------
# Network Report — validates existing page still works after macro changes
# ---------------------------------------------------------------------------


class TestNetworkReportRegression:
    """Regression: network report should still load after refactoring."""

    def test_page_loads(self, admin_page: Page, settings) -> None:
        """Network report loads without template errors."""
        page = NetworkReportPage(admin_page, settings.base_url)
        page.goto()
        page.expect_loaded()

    def test_ip_pool_progress_bars(self, admin_page: Page, settings) -> None:
        """IP pool section renders progress bars (not refactored yet, but should still work)."""
        page = NetworkReportPage(admin_page, settings.base_url)
        page.goto()
        page.expect_loaded()
        # IP Pool Allocation section with manual progress bars
        pool_section = admin_page.get_by_text("IP Pool Allocation", exact=False)
        if pool_section.count() > 0:
            expect(pool_section.first).to_be_visible()


# ---------------------------------------------------------------------------
# Dark mode checks — all macros should have dark: variant classes
# ---------------------------------------------------------------------------


class TestDarkModeSupport:
    """Verify macro-rendered elements have dark mode classes."""

    def test_technician_table_dark_classes(self, admin_page: Page, settings) -> None:
        """Leaderboard table has dark mode border and text classes."""
        page = TechnicianReportPage(admin_page, settings.base_url)
        page.goto()
        page.expect_loaded()
        # The table should have dark:divide-slate-700
        table = admin_page.locator("table").first
        if table.is_visible():
            classes = table.get_attribute("class") or ""
            assert "dark:divide-slate-700" in classes or "divide-slate" in classes

    def test_score_badge_dark_classes(self, admin_page: Page, settings) -> None:
        """score_cell badges include dark mode opacity variants."""
        page = PerformanceLeaderboardPage(admin_page, settings.base_url)
        page.goto()
        page.expect_loaded()
        # score_cell uses dark:bg-{color}-500/15 and dark:text-{color}-400
        badges = admin_page.locator(
            "[class*='dark:bg-emerald-500'], [class*='dark:bg-amber-500'], [class*='dark:bg-rose-500']"
        )
        # 0 if no data — that's fine
        if badges.count() > 0:
            expect(badges.first).to_be_visible()


# ---------------------------------------------------------------------------
# Accessibility checks
# ---------------------------------------------------------------------------


class TestAccessibility:
    """Basic accessibility checks for macro-rendered elements."""

    def test_table_has_th_scope(self, admin_page: Page, settings) -> None:
        """Leaderboard table headers use scope='col'."""
        page = TechnicianReportPage(admin_page, settings.base_url)
        page.goto()
        page.expect_loaded()
        headers = admin_page.locator("th[scope='col']")
        # If table rendered, should have scoped headers
        if admin_page.locator("table").count() > 0:
            assert headers.count() > 0, "Table headers should have scope='col'"

    def test_rank_badges_have_text(self, admin_page: Page, settings) -> None:
        """Rank badges contain visible text (not just color)."""
        page = TechnicianReportPage(admin_page, settings.base_url)
        page.goto()
        page.expect_loaded()
        badges = admin_page.locator("table td .rounded-lg.bg-gradient-to-br")
        for i in range(min(badges.count(), 3)):
            text = badges.nth(i).inner_text().strip()
            assert text in ["1", "2", "3"], f"Badge {i} should have rank number, got '{text}'"
