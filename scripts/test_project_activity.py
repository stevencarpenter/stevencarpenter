"""Unit tests for the pure functions of project_activity."""

import unittest
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

import project_activity as pa

# Sunday 2025-11-16 is 51 weeks before Sunday 2026-11-08.
START = datetime(2025, 11, 16, tzinfo=UTC)
SVG = "{http://www.w3.org/2000/svg}"
FONT = "-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif"
CELL_STYLES = [
    ("#8b949e", "0.25"),
    ("#2da44e", "0.35"),
    ("#2da44e", "0.55"),
    ("#2da44e", "0.8"),
    ("#2da44e", "1"),
]
EMPTY = [0] * 52


def at(text: str) -> datetime:
    return datetime.fromisoformat(text)


def find(svg: str, tag: str) -> list[ET.Element]:
    return ET.fromstring(svg).findall(SVG + tag)


def commit(date: str) -> dict[str, str]:
    return {"__typename": "Commit", "committedDate": date}


class WeekWindowTest(unittest.TestCase):
    def test_window_starts_51_weeks_before_the_sunday_of_the_current_week(self) -> None:
        cases = {
            "2026-11-11T15:30:00Z": START,  # Wednesday
            "2026-11-08T00:00:00Z": START,  # Sunday 00:00:00 starts the last week
            "2026-11-07T23:59:59Z": datetime(2025, 11, 9, tzinfo=UTC),  # Saturday
            "2026-11-07T18:30:00-07:00": START,  # Saturday local time, Sunday in UTC
        }
        for now, want in cases.items():
            with self.subTest(now=now):
                self.assertEqual(pa.window_start(at(now)), want)

    def test_week_index(self) -> None:
        cases = {
            "2025-11-16T00:00:00Z": 0,
            "2025-11-22T23:59:59Z": 0,
            "2025-11-23T00:00:00Z": 1,  # Sunday 00:00:00 UTC starts a new week
            "2026-11-07T18:30:00-07:00": 51,  # local date is in week 50, UTC date is in week 51
            "2026-11-11T12:00:00Z": 51,
            "2026-11-14T23:59:59Z": 51,
            "2025-11-15T23:59:59Z": None,  # before the window
            "2026-11-15T00:00:00Z": None,  # after the window
        }
        for moment, want in cases.items():
            with self.subTest(moment=moment):
                self.assertEqual(pa.week_index(at(moment), START), want)

    def test_week_counts_ignore_moments_outside_the_window(self) -> None:
        moments = [
            at("2025-11-15T23:59:59Z"),
            at("2025-11-16T00:00:00Z"),
            at("2025-11-20T08:00:00-07:00"),
            at("2026-11-11T12:00:00Z"),
            at("2026-11-15T00:00:00Z"),
        ]
        self.assertEqual(pa.week_counts(moments, START), [2] + [0] * 50 + [1])


class CommitLevelTest(unittest.TestCase):
    def test_levels(self) -> None:
        cases = {0: 0, 1: 1, 2: 1, 3: 2, 6: 2, 7: 3, 14: 3, 15: 4, 400: 4}
        for count, want in cases.items():
            with self.subTest(count=count):
                self.assertEqual(pa.commit_level(count), want)


class VersionTagTest(unittest.TestCase):
    def test_version_tag_names(self) -> None:
        for name in ["v0.6.0", "v.0.2.0", "2026.10.17", "2026.07.10.post1"]:
            with self.subTest(name=name):
                self.assertTrue(pa.is_version_tag(name))

    def test_other_tag_names(self) -> None:
        names = [
            "pre-standalone-2026-07-18",
            "no-mistakes-abandoned/fix/prime-decision-counters/0b9b80",
            "latest",
            "v1",
        ]
        for name in names:
            with self.subTest(name=name):
                self.assertFalse(pa.is_version_tag(name))

    def test_tag_date(self) -> None:
        commit_date = "2026-11-01T09:00:00Z"
        cases = [
            ("lightweight tag", commit("2026-11-08T10:43:16Z"), at("2026-11-08T10:43:16Z")),
            (
                "annotated tag with a tagger date",
                {
                    "__typename": "Tag",
                    "tagger": {"date": "2026-11-08T16:08:43-07:00"},
                    "target": {"committedDate": commit_date},
                },
                at("2026-11-08T23:08:43Z"),
            ),
            (
                "annotated tag without a tagger",
                {"__typename": "Tag", "tagger": None, "target": {"committedDate": commit_date}},
                at(commit_date),
            ),
            (
                "annotated tag without a tagger date",
                {"__typename": "Tag", "tagger": {"date": None}, "target": {"committedDate": commit_date}},
                at(commit_date),
            ),
            ("annotated tag of a tree", {"__typename": "Tag", "tagger": None, "target": {}}, None),
            ("lightweight tag of a tree", {"__typename": "Tree"}, None),
            ("no target", None, None),
        ]
        for name, target, want in cases:
            with self.subTest(name=name):
                self.assertEqual(pa.tag_date(target), want)

    def test_version_tags_keep_dated_version_tags_only(self) -> None:
        nodes = [
            {"name": "latest", "target": commit("2026-11-10T12:00:00Z")},
            {"name": "v0.10.0", "target": commit("2026-11-09T12:00:00Z")},
            {"name": "v0.11.0", "target": {"__typename": "Tree"}},
            {
                "name": "v0.9.0",
                "target": {
                    "__typename": "Tag",
                    "tagger": {"date": "2025-12-01T12:00:00Z"},
                    "target": {"committedDate": "2025-11-30T12:00:00Z"},
                },
            },
        ]
        want = [(at("2026-11-09T12:00:00Z"), "v0.10.0"), (at("2025-12-01T12:00:00Z"), "v0.9.0")]
        self.assertEqual(pa.version_tags(nodes), want)

    def test_newest_version_tag_has_the_latest_date(self) -> None:
        tags = [
            (at("2026-03-01T12:00:00Z"), "v0.9.0"),
            (at("2026-10-17T12:00:00Z"), "v0.10.0"),
            (at("2026-07-10T12:00:00Z"), "v0.9.1"),
        ]
        self.assertEqual(pa.newest_version_tag(tags), "v0.10.0")

    def test_newest_version_tag_of_no_tags(self) -> None:
        self.assertIsNone(pa.newest_version_tag([]))

    def test_release_counts_use_version_tags_inside_the_window(self) -> None:
        nodes = [
            {"name": "v0.1.0", "target": commit("2025-11-15T23:59:59Z")},  # before the window
            {"name": "v0.2.0", "target": commit("2025-11-16T00:00:00Z")},
            {"name": "v0.2.1", "target": commit("2025-11-18T00:00:00Z")},
            {"name": "latest", "target": commit("2025-11-19T00:00:00Z")},
            {"name": "v0.3.0", "target": commit("2026-11-11T00:00:00Z")},
        ]
        dates = [date for date, _ in pa.version_tags(nodes)]
        self.assertEqual(pa.week_counts(dates, START), [2] + [0] * 50 + [1])


class LatestVersionTest(unittest.TestCase):
    def test_tag_with_a_release_page(self) -> None:
        self.assertEqual(
            pa.latest_version("hippo", "v0.41.0", True, "main"),
            ("v0.41.0", "https://github.com/stevencarpenter/hippo/releases/tag/v0.41.0"),
        )

    def test_tag_without_a_release_page(self) -> None:
        self.assertEqual(
            pa.latest_version("nuv", "2026.10.17", False, "main"),
            ("2026.10.17", "https://github.com/stevencarpenter/nuv/tree/2026.10.17"),
        )

    def test_no_version_tag(self) -> None:
        self.assertEqual(
            pa.latest_version("dotfiles", None, False, "main"),
            ("main", "https://github.com/stevencarpenter/dotfiles/tree/main"),
        )

    def test_tag_and_branch_are_quoted_in_the_link_only(self) -> None:
        self.assertEqual(
            pa.latest_version("nuv", "v1.2.3+meta", True, "main"),
            ("v1.2.3+meta", "https://github.com/stevencarpenter/nuv/releases/tag/v1.2.3%2Bmeta"),
        )
        self.assertEqual(
            pa.latest_version("nuv", None, False, "dev/next#1"),
            ("dev/next#1", "https://github.com/stevencarpenter/nuv/tree/dev/next%231"),
        )


class ReplaceBlockTest(unittest.TestCase):
    README = (
        "# Title\n\nintro\n\n"
        "<!-- project-activity:start -->\nold row\nold legend\n<!-- project-activity:end -->\n"
        "\n## Sponsor\n"
    )

    def test_replaces_the_text_between_the_markers_only(self) -> None:
        want = (
            "# Title\n\nintro\n\n"
            "<!-- project-activity:start -->\nnew table\n<!-- project-activity:end -->\n"
            "\n## Sponsor\n"
        )
        self.assertEqual(pa.replace_block(self.README, "new table"), want)

    def test_fills_an_empty_block(self) -> None:
        readme = "a\n<!-- project-activity:start --><!-- project-activity:end -->\nb\n"
        want = "a\n<!-- project-activity:start -->\nnew\n<!-- project-activity:end -->\nb\n"
        self.assertEqual(pa.replace_block(readme, "new"), want)

    def test_second_run_changes_nothing(self) -> None:
        once = pa.replace_block(self.README, "new table")
        self.assertEqual(pa.replace_block(once, "new table"), once)

    def test_missing_or_reversed_markers_raise(self) -> None:
        cases = {
            "no start marker": "a\n<!-- project-activity:end -->\n",
            "no end marker": "a\n<!-- project-activity:start -->\n",
            "no markers": "a\n",
            "end before start": "<!-- project-activity:end -->\nx\n<!-- project-activity:start -->\n",
        }
        for name, readme in cases.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                pa.replace_block(readme, "new")


class StripSvgTest(unittest.TestCase):
    def test_root(self) -> None:
        root = ET.fromstring(pa.strip_svg("hippo", EMPTY, EMPTY))
        self.assertEqual(root.tag, SVG + "svg")
        self.assertEqual(
            root.attrib,
            {
                "width": "468",
                "height": "24",
                "viewBox": "0 0 468 24",
                "role": "img",
                "aria-label": "hippo: weekly commits and releases, last 52 weeks",
            },
        )

    def test_label_is_escaped(self) -> None:
        root = ET.fromstring(pa.strip_svg('a<b&"c', EMPTY, EMPTY))
        self.assertEqual(root.get("aria-label"), 'a<b&"c: weekly commits and releases, last 52 weeks')

    def test_one_cell_for_each_week_styled_by_commit_level(self) -> None:
        rects = find(pa.strip_svg("hippo", [0, 1, 3, 7, 15] + [0] * 47, EMPTY), "rect")
        self.assertEqual(len(rects), 52)
        self.assertEqual([rects[i].get("x") for i in (0, 1, 2, 51)], ["0", "9", "18", "459"])
        self.assertEqual(
            {(r.get("y"), r.get("width"), r.get("height"), r.get("rx")) for r in rects},
            {("10", "7", "13", "2")},
        )
        self.assertEqual([(r.get("fill"), r.get("fill-opacity")) for r in rects[:6]], CELL_STYLES + CELL_STYLES[:1])

    def test_no_dot_without_a_release(self) -> None:
        self.assertEqual(find(pa.strip_svg("hippo", [5] * 52, EMPTY), "circle"), [])

    def test_dot_grows_with_the_number_of_version_tags(self) -> None:
        releases = [1, 0, 3] + [0] * 48 + [9]
        circles = find(pa.strip_svg("hippo", EMPTY, releases), "circle")
        self.assertEqual(
            [c.attrib for c in circles],
            [
                {"cx": "3.5", "cy": "4.5", "r": "2.35", "fill": "#e16f24"},
                {"cx": "21.5", "cy": "4.5", "r": "3.05", "fill": "#e16f24"},
                {"cx": "462.5", "cy": "4.5", "r": "4", "fill": "#e16f24"},
            ],
        )


class MonthsSvgTest(unittest.TestCase):
    def test_root(self) -> None:
        root = ET.fromstring(pa.months_svg(START))
        self.assertEqual(root.tag, SVG + "svg")
        self.assertEqual((root.get("width"), root.get("height")), ("468", "14"))

    def test_label_at_each_week_that_starts_a_new_month(self) -> None:
        texts = find(pa.months_svg(START), "text")
        # Week 0 starts on 2025-11-16 and has no label. Week 3 starts on 2025-12-07.
        want = [
            ("27", "Dec"),
            ("63", "Jan"),
            ("99", "Feb"),
            ("135", "Mar"),
            ("180", "Apr"),
            ("216", "May"),
            ("261", "Jun"),
            ("297", "Jul"),
            ("333", "Aug"),
            ("378", "Sep"),
            ("414", "Oct"),
            # Week 50 is at x 450. The label moves left so that the image edge does not cut it.
            ("446", "Nov"),
        ]
        self.assertEqual([(t.get("x"), t.text) for t in texts], want)
        self.assertEqual(
            {(t.get("y"), t.get("font-size"), t.get("font-family"), t.get("fill")) for t in texts},
            {("11", "11", FONT, "#727a84")},
        )

    def test_label_of_a_month_that_starts_in_the_last_week_stays_inside_the_image(self) -> None:
        # Week 51 of this window starts on Sunday 2026-11-01.
        texts = find(pa.months_svg(datetime(2025, 11, 9, tzinfo=UTC)), "text")
        self.assertEqual((texts[-1].get("x"), texts[-1].text), ("446", "Nov"))


class LegendSvgTest(unittest.TestCase):
    def test_root_uses_the_exposed_width(self) -> None:
        root = ET.fromstring(pa.legend_svg())
        self.assertEqual(root.tag, SVG + "svg")
        self.assertEqual((root.get("width"), root.get("height")), (str(pa.LEGEND_WIDTH), "14"))

    def test_content(self) -> None:
        svg = pa.legend_svg()
        texts = find(svg, "text")
        self.assertEqual(
            [t.text for t in texts],
            ["Weekly commits:", "0", "1 to 2", "3 to 6", "7 to 14", "15 or more", "release"],
        )
        self.assertEqual(
            {(t.get("font-size"), t.get("font-family"), t.get("fill")) for t in texts},
            {("11", FONT, "#727a84")},
        )
        rects = find(svg, "rect")
        self.assertEqual([(r.get("fill"), r.get("fill-opacity")) for r in rects], CELL_STYLES)
        self.assertEqual({(r.get("width"), r.get("height"), r.get("rx")) for r in rects}, {("7", "13", "2")})
        self.assertEqual([(c.get("r"), c.get("fill")) for c in find(svg, "circle")], [("3.5", "#e16f24")])

    def test_items_do_not_overlap(self) -> None:
        # 6.5 px for each character is a wide estimate for 11 px sans-serif text.
        spans: list[tuple[float, float]] = []
        for item in ET.fromstring(pa.legend_svg()):
            if item.tag == SVG + "text":
                left = float(item.get("x", ""))
                spans.append((left, left + 6.5 * len(item.text or "")))
            elif item.tag == SVG + "rect":
                left = float(item.get("x", ""))
                spans.append((left, left + float(item.get("width", ""))))
            else:
                center, radius = float(item.get("cx", "")), float(item.get("r", ""))
                spans.append((center - radius, center + radius))
        self.assertEqual(len(spans), 13)
        self.assertGreaterEqual(spans[0][0], 0)
        for (_, right), (left, _) in zip(spans, spans[1:]):
            self.assertLess(right, left)
        self.assertLessEqual(spans[-1][1], pa.LEGEND_WIDTH)


class ReadmeBlockTest(unittest.TestCase):
    def test_table_and_legend(self) -> None:
        projects = [
            pa.Project(
                repo="hippo",
                commits=[0] * 50 + [500, 12],
                releases=[70] + [0] * 50 + [3],
                label="v0.41.0",
                link="https://github.com/stevencarpenter/hippo/releases/tag/v0.41.0",
            ),
            pa.Project(
                repo="dotfiles",
                commits=EMPTY,
                releases=EMPTY,
                label="main",
                link="https://github.com/stevencarpenter/dotfiles/tree/main",
            ),
        ]
        want = "\n".join(
            [
                "| Project | Weekly&nbsp;commits&nbsp;and&nbsp;releases&nbsp;in&nbsp;the&nbsp;last&nbsp;52&nbsp;weeks<br>"
                '<img src="assets/activity/months.svg" width="468" height="14"'
                ' alt="Month axis for the last 52 weeks"> | Latest version |',
                "| --- | --- | --- |",
                "| [**hippo**](https://github.com/stevencarpenter/hippo)"
                ' | <img src="assets/activity/hippo.svg" width="468" height="24"'
                ' alt="hippo: 512 commits and 73 releases in the last 52 weeks">'
                " | [`v0.41.0`](https://github.com/stevencarpenter/hippo/releases/tag/v0.41.0) |",
                "| [**dotfiles**](https://github.com/stevencarpenter/dotfiles)"
                ' | <img src="assets/activity/dotfiles.svg" width="468" height="24"'
                ' alt="dotfiles: 0 commits and 0 releases in the last 52 weeks">'
                " | [`main`](https://github.com/stevencarpenter/dotfiles/tree/main) |",
                "",
                f'<img src="assets/activity/legend.svg" width="{pa.LEGEND_WIDTH}" height="14"'
                ' alt="Each cell is one week. A stronger green means more commits.'
                ' An orange dot marks a release.">',
            ]
        )
        self.assertEqual(pa.readme_block(projects), want)

    def test_alt_text_uses_the_singular_for_a_count_of_one(self) -> None:
        project = pa.Project(repo="nuv", commits=[1] + [0] * 51, releases=[0] * 51 + [1], label="main", link="x")
        self.assertIn('alt="nuv: 1 commit and 1 release in the last 52 weeks"', pa.readme_block([project]))


if __name__ == "__main__":
    unittest.main()
