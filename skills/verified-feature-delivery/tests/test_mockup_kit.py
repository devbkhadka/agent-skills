"""Unit tests for Reusable React Mockup Kit and Screen Catalogue.

Tests layout primitives, interactive overlay components, targeted numbered
annotation badge system, screen catalogue manifest, and standalone HTML bundle generator.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
MOCKUP_KIT_DIR = SKILL_DIR / "mockup-kit"

from lib.mockup_bundle import render_mockup_bundle, default_sample_screens


class TestMockupKitPrimitivesAndStructure(unittest.TestCase):
    def test_mockup_kit_directory_and_sources_exist(self):
        self.assertTrue(MOCKUP_KIT_DIR.is_dir(), "mockup-kit directory must exist")
        src_dir = MOCKUP_KIT_DIR / "src"
        self.assertTrue(src_dir.is_dir(), "src directory must exist")
        self.assertTrue((src_dir / "styles.css").is_file(), "styles.css must exist")
        self.assertTrue((src_dir / "types.ts").is_file(), "types.ts must exist")
        self.assertTrue((src_dir / "index.ts").is_file(), "index.ts must exist")

    def test_layout_primitives_present(self):
        primitives_dir = MOCKUP_KIT_DIR / "src" / "primitives"
        required_primitives = [
            "Box.tsx",
            "Flex.tsx",
            "Grid.tsx",
            "Stack.tsx",
            "Card.tsx",
            "Button.tsx",
            "TextPlaceholder.tsx",
            "ImagePlaceholder.tsx",
            "index.ts",
        ]
        for name in required_primitives:
            p = primitives_dir / name
            self.assertTrue(p.is_file(), f"Primitive {name} must exist under primitives/")
            content = p.read_text(encoding="utf-8")
            self.assertIn("export ", content, f"{name} must export primitive component")

    def test_interactive_overlays_and_annotations_present(self):
        components_dir = MOCKUP_KIT_DIR / "src" / "components"
        required_components = [
            "Modal.tsx",
            "Dialog.tsx",
            "Drawer.tsx",
            "Accordion.tsx",
            "Dropdown.tsx",
            "Annotation.tsx",
            "index.ts",
        ]
        for name in required_components:
            p = components_dir / name
            self.assertTrue(p.is_file(), f"Component {name} must exist under components/")
            content = p.read_text(encoding="utf-8")
            self.assertIn("export ", content, f"{name} must export component")

    def test_catalogue_and_viewport_frame_present(self):
        catalogue_dir = MOCKUP_KIT_DIR / "src" / "catalogue"
        required_catalogue = [
            "ScreenCatalogue.tsx",
            "ViewportFrame.tsx",
            "AnnotationInspector.tsx",
            "index.ts",
        ]
        for name in required_catalogue:
            p = catalogue_dir / name
            self.assertTrue(p.is_file(), f"Catalogue file {name} must exist under catalogue/")
            content = p.read_text(encoding="utf-8")
            self.assertIn("export ", content, f"{name} must export catalogue component")

    def test_example_screens_present(self):
        examples_dir = MOCKUP_KIT_DIR / "src" / "examples"
        self.assertTrue((examples_dir / "OrderReconciliationScreen.tsx").is_file())
        self.assertTrue((examples_dir / "InvoiceSettlementMobileScreen.tsx").is_file())
        self.assertTrue((examples_dir / "index.ts").is_file())

        order_screen = (examples_dir / "OrderReconciliationScreen.tsx").read_text(encoding="utf-8")
        # Demonstrates layout primitives, openable modal, and targeted context annotations
        self.assertIn("AnnotationBadge", order_screen)
        self.assertIn("Modal", order_screen)
        self.assertIn("Accordion", order_screen)
        self.assertIn("Grid", order_screen)
        self.assertIn("Flex", order_screen)
        self.assertIn("Card", order_screen)


class TestTargetedAnnotationSystem(unittest.TestCase):
    def test_sample_screens_declare_targeted_annotations(self):
        screens = default_sample_screens()
        self.assertGreaterEqual(len(screens), 2, "At least 2 example screens required")

        # Desktop order reconciliation screen
        desktop_screen = screens[0]
        self.assertEqual(desktop_screen["viewport"], "desktop_1280x800")
        self.assertEqual(desktop_screen["sliceId"], "slice_settlement_reconciliation")
        self.assertIn("annotations", desktop_screen)
        annotations = desktop_screen["annotations"]
        self.assertEqual(len(annotations), 3)

        # Check annotation 1 fields
        ann1 = annotations[0]
        self.assertEqual(ann1["number"], 1)
        self.assertIn("Discrepancy Tolerance", ann1["title"])
        self.assertTrue(len(ann1["businessIntent"]) > 20)
        self.assertIn("LedgerReconciliationRule", ann1["domainEntityMapping"])
        self.assertIn("TransactionBatchImportedEvent", ann1["projectionSource"])

        # Check annotation 2 fields
        ann2 = annotations[1]
        self.assertEqual(ann2["number"], 2)
        self.assertIn("Tax", ann2["title"])
        self.assertIn("TaxJurisdictionAllocationValueObject", ann2["domainEntityMapping"])

        # Check annotation 3 fields
        ann3 = annotations[2]
        self.assertEqual(ann3["number"], 3)
        self.assertIn("Settlement", ann3["title"])
        self.assertIn("SettlementBatchAggregate", ann3["domainEntityMapping"])


class TestStandaloneMockupBundleGenerator(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vfd-mockup-test-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_renders_self_contained_html_bundle(self):
        slug = "test-settlement-slice"
        out_dir = self.tmp / ".verified-feature-delivery" / slug / "mockups"
        bundle_file = render_mockup_bundle(
            feature_slug=slug,
            feature_title="Test Settlement Feature",
            out_dir=out_dir,
        )

        self.assertTrue(bundle_file.is_file(), "Bundle index.html must be created on disk")
        content = bundle_file.read_text(encoding="utf-8")

        # Check HTML structure
        self.assertIn("<!DOCTYPE html>", content)
        self.assertIn(f"Test Settlement Feature — Screen Catalogue ({slug})", content)
        self.assertIn("vfd-catalogue-root", content)
        self.assertIn("vfd-annotation-badge", content)
        self.assertIn("vfd-annotation-popover", content)
        self.assertIn("desktop_1280x800", content)
        self.assertIn("mobile_375x667", content)
        self.assertIn("Order Reconciliation & Settlement Ledger", content)
        self.assertIn("Tax Jurisdiction Reallocation", content)

    def test_bundle_contains_viewport_presets_and_screens(self):
        slug = "viewport-test"
        out_dir = self.tmp / "custom-mockups"
        screens = default_sample_screens()
        bundle_file = render_mockup_bundle(
            feature_slug=slug,
            screens=screens,
            out_dir=out_dir,
        )

        content = bundle_file.read_text(encoding="utf-8")
        self.assertIn("desktop_1280x800", content)
        self.assertIn("mobile_375x667", content)
        self.assertIn("tablet_768x1024", content)
        self.assertIn("Discrepancy Tolerance Threshold", content)


if __name__ == "__main__":
    unittest.main()
