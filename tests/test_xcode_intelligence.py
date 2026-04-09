"""Tests for Xcode / iOS project intelligence.

Uses synthetic project fixtures — no real Xcode installation required.
"""
from __future__ import annotations

import plistlib
import tempfile
import unittest
from pathlib import Path


# ── Synthetic pbxproj fixture ───────────────────────────────────────

SYNTHETIC_PBXPROJ = """\
// !$*UTF8*$!
{
	archiveVersion = 1;
	classes = {
	};
	objectVersion = 56;
	objects = {

/* Begin PBXNativeTarget section */
		A1B2C3D4E5F60001 /* MyApp */ = {
			isa = PBXNativeTarget;
			buildConfigurationList = A1B2C3D4E5F60010 /* Build configuration list for PBXNativeTarget "MyApp" */;
			buildPhases = (
			);
			buildRules = (
			);
			dependencies = (
			);
			name = MyApp;
			productName = MyApp;
			productReference = A1B2C3D4E5F60099 /* MyApp.app */;
			productType = "com.apple.product-type.application";
		};
		A1B2C3D4E5F60002 /* MyAppTests */ = {
			isa = PBXNativeTarget;
			buildConfigurationList = A1B2C3D4E5F60020 /* Build configuration list for PBXNativeTarget "MyAppTests" */;
			buildPhases = (
			);
			buildRules = (
			);
			dependencies = (
			);
			name = MyAppTests;
			productName = MyAppTests;
			productType = "com.apple.product-type.bundle.unit-test";
		};
		A1B2C3D4E5F60003 /* MyAppUITests */ = {
			isa = PBXNativeTarget;
			buildConfigurationList = A1B2C3D4E5F60030 /* Build configuration list for PBXNativeTarget "MyAppUITests" */;
			buildPhases = (
			);
			buildRules = (
			);
			dependencies = (
			);
			name = MyAppUITests;
			productName = MyAppUITests;
			productType = "com.apple.product-type.bundle.ui-testing";
		};
/* End PBXNativeTarget section */

/* Begin PBXProject section */
		A1B2C3D4E5F60000 /* Project object */ = {
			isa = PBXProject;
			buildConfigurationList = A1B2C3D4E5F60040 /* Build configuration list for PBXProject "MyApp" */;
			compatibilityVersion = "Xcode 14.0";
			mainGroup = A1B2C3D4E5F60098;
			targets = (
				A1B2C3D4E5F60001 /* MyApp */,
				A1B2C3D4E5F60002 /* MyAppTests */,
				A1B2C3D4E5F60003 /* MyAppUITests */,
			);
		};
/* End PBXProject section */

/* Begin XCBuildConfiguration section */
		A1B2C3D4E5F60011 /* Debug */ = {
			isa = XCBuildConfiguration;
			buildSettings = {
				PRODUCT_BUNDLE_IDENTIFIER = "com.example.myapp";
				CODE_SIGN_STYLE = Automatic;
				DEVELOPMENT_TEAM = ABCD1234EF;
				INFOPLIST_FILE = "MyApp/Info.plist";
				CODE_SIGN_ENTITLEMENTS = "MyApp/MyApp.entitlements";
				SDKROOT = iphoneos;
			};
			name = Debug;
		};
		A1B2C3D4E5F60012 /* Release */ = {
			isa = XCBuildConfiguration;
			buildSettings = {
				PRODUCT_BUNDLE_IDENTIFIER = "com.example.myapp";
				CODE_SIGN_STYLE = Automatic;
				DEVELOPMENT_TEAM = ABCD1234EF;
				INFOPLIST_FILE = "MyApp/Info.plist";
				CODE_SIGN_ENTITLEMENTS = "MyApp/MyApp.entitlements";
				SDKROOT = iphoneos;
			};
			name = Release;
		};
		A1B2C3D4E5F60021 /* Debug */ = {
			isa = XCBuildConfiguration;
			buildSettings = {
				PRODUCT_BUNDLE_IDENTIFIER = "com.example.myapp.tests";
				TEST_HOST = "$(BUILT_PRODUCTS_DIR)/MyApp.app/$(BUNDLE_EXECUTABLE_FOLDER_PATH)/MyApp";
			};
			name = Debug;
		};
		A1B2C3D4E5F60022 /* Release */ = {
			isa = XCBuildConfiguration;
			buildSettings = {
				PRODUCT_BUNDLE_IDENTIFIER = "com.example.myapp.tests";
			};
			name = Release;
		};
		A1B2C3D4E5F60031 /* Debug */ = {
			isa = XCBuildConfiguration;
			buildSettings = {
				PRODUCT_BUNDLE_IDENTIFIER = "com.example.myapp.uitests";
			};
			name = Debug;
		};
		A1B2C3D4E5F60032 /* Release */ = {
			isa = XCBuildConfiguration;
			buildSettings = {
				PRODUCT_BUNDLE_IDENTIFIER = "com.example.myapp.uitests";
			};
			name = Release;
		};
		A1B2C3D4E5F60041 /* Debug */ = {
			isa = XCBuildConfiguration;
			buildSettings = {
				ALWAYS_SEARCH_USER_PATHS = NO;
			};
			name = Debug;
		};
		A1B2C3D4E5F60042 /* Release */ = {
			isa = XCBuildConfiguration;
			buildSettings = {
				ALWAYS_SEARCH_USER_PATHS = NO;
			};
			name = Release;
		};
/* End XCBuildConfiguration section */

/* Begin XCConfigurationList section */
		A1B2C3D4E5F60010 /* Build configuration list for PBXNativeTarget "MyApp" */ = {
			isa = XCConfigurationList;
			buildConfigurations = (
				A1B2C3D4E5F60011 /* Debug */,
				A1B2C3D4E5F60012 /* Release */,
			);
			defaultConfigurationName = Release;
		};
		A1B2C3D4E5F60020 /* Build configuration list for PBXNativeTarget "MyAppTests" */ = {
			isa = XCConfigurationList;
			buildConfigurations = (
				A1B2C3D4E5F60021 /* Debug */,
				A1B2C3D4E5F60022 /* Release */,
			);
			defaultConfigurationName = Release;
		};
		A1B2C3D4E5F60030 /* Build configuration list for PBXNativeTarget "MyAppUITests" */ = {
			isa = XCConfigurationList;
			buildConfigurations = (
				A1B2C3D4E5F60031 /* Debug */,
				A1B2C3D4E5F60032 /* Release */,
			);
			defaultConfigurationName = Release;
		};
		A1B2C3D4E5F60040 /* Build configuration list for PBXProject "MyApp" */ = {
			isa = XCConfigurationList;
			buildConfigurations = (
				A1B2C3D4E5F60041 /* Debug */,
				A1B2C3D4E5F60042 /* Release */,
			);
			defaultConfigurationName = Release;
		};
/* End XCConfigurationList section */

	};
}
"""

SYNTHETIC_XCSCHEME = """\
<?xml version="1.0" encoding="UTF-8"?>
<Scheme LastUpgradeVersion="1500" version="1.7">
   <BuildAction parallelizeBuildables="YES" buildImplicitDependencies="YES">
      <BuildActionEntries>
         <BuildActionEntry buildForTesting="YES" buildForRunning="YES">
            <BuildableReference
               BuildableIdentifier="primary"
               BlueprintIdentifier="A1B2C3D4E5F60001"
               BuildableName="MyApp.app"
               BlueprintName="MyApp"
               ReferencedContainer="container:MyApp.xcodeproj">
            </BuildableReference>
         </BuildActionEntry>
      </BuildActionEntries>
   </BuildAction>
   <TestAction buildConfiguration="Debug">
      <Testables>
         <TestableReference skipped="NO">
            <BuildableReference
               BuildableIdentifier="primary"
               BlueprintIdentifier="A1B2C3D4E5F60002"
               BuildableName="MyAppTests.xctest"
               BlueprintName="MyAppTests"
               ReferencedContainer="container:MyApp.xcodeproj">
            </BuildableReference>
         </TestableReference>
      </Testables>
   </TestAction>
   <LaunchAction buildConfiguration="Debug">
      <BuildableProductRunnable runnableDebuggingMode="0">
         <BuildableReference
            BuildableIdentifier="primary"
            BlueprintIdentifier="A1B2C3D4E5F60001"
            BuildableName="MyApp.app"
            BlueprintName="MyApp"
            ReferencedContainer="container:MyApp.xcodeproj">
         </BuildableReference>
      </BuildableProductRunnable>
   </LaunchAction>
</Scheme>
"""


def _create_synthetic_xcode_project(root: Path) -> Path:
    """Create a minimal synthetic .xcodeproj structure under root."""
    proj_dir = root / "MyApp.xcodeproj"
    proj_dir.mkdir(parents=True)

    # pbxproj
    (proj_dir / "project.pbxproj").write_text(SYNTHETIC_PBXPROJ, encoding="utf-8")

    # Shared schemes
    scheme_dir = proj_dir / "xcshareddata" / "xcschemes"
    scheme_dir.mkdir(parents=True)
    (scheme_dir / "MyApp.xcscheme").write_text(SYNTHETIC_XCSCHEME, encoding="utf-8")

    # Info.plist (binary plist via plistlib)
    info_dir = root / "MyApp"
    info_dir.mkdir(exist_ok=True)
    plist_data = {
        "CFBundleIdentifier": "$(PRODUCT_BUNDLE_IDENTIFIER)",
        "CFBundleName": "$(PRODUCT_NAME)",
        "CFBundleDisplayName": "My App",
        "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "1",
        "MinimumOSVersion": "16.0",
        "UIDeviceFamily": [1, 2],
        "UILaunchStoryboardName": "LaunchScreen",
    }
    with (info_dir / "Info.plist").open("wb") as f:
        plistlib.dump(plist_data, f)

    # Entitlements
    ent_data = {
        "aps-environment": "development",
        "com.apple.developer.associated-domains": ["applinks:example.com"],
    }
    with (info_dir / "MyApp.entitlements").open("wb") as f:
        plistlib.dump(ent_data, f)

    return root


# ── pbxproj parsing tests ──────────────────────────────────────────


class TestPbxprojParsing(unittest.TestCase):
    """Parse the synthetic pbxproj and validate extracted targets."""

    def test_parse_targets(self):
        from boss.intelligence.xcode import parse_pbxproj

        targets, configs, errors = parse_pbxproj(SYNTHETIC_PBXPROJ)
        self.assertEqual(len(targets), 3)

        names = {t.name for t in targets}
        self.assertEqual(names, {"MyApp", "MyAppTests", "MyAppUITests"})

    def test_app_target_identification(self):
        from boss.intelligence.xcode import parse_pbxproj

        targets, _, _ = parse_pbxproj(SYNTHETIC_PBXPROJ)
        app_targets = [t for t in targets if t.is_app_target]
        self.assertEqual(len(app_targets), 1)
        self.assertEqual(app_targets[0].name, "MyApp")

    def test_test_target_identification(self):
        from boss.intelligence.xcode import parse_pbxproj

        targets, _, _ = parse_pbxproj(SYNTHETIC_PBXPROJ)
        test_targets = [t for t in targets if t.is_test_target]
        self.assertEqual(len(test_targets), 2)
        test_names = {t.name for t in test_targets}
        self.assertEqual(test_names, {"MyAppTests", "MyAppUITests"})

    def test_bundle_identifier(self):
        from boss.intelligence.xcode import parse_pbxproj

        targets, _, _ = parse_pbxproj(SYNTHETIC_PBXPROJ)
        app = next(t for t in targets if t.name == "MyApp")
        self.assertEqual(app.bundle_identifier, "com.example.myapp")

    def test_signing_style(self):
        from boss.intelligence.xcode import parse_pbxproj

        targets, _, _ = parse_pbxproj(SYNTHETIC_PBXPROJ)
        app = next(t for t in targets if t.name == "MyApp")
        self.assertEqual(app.signing_style, "automatic")

    def test_team_id(self):
        from boss.intelligence.xcode import parse_pbxproj

        targets, _, _ = parse_pbxproj(SYNTHETIC_PBXPROJ)
        app = next(t for t in targets if t.name == "MyApp")
        self.assertEqual(app.team_id, "ABCD1234EF")

    def test_entitlements_file(self):
        from boss.intelligence.xcode import parse_pbxproj

        targets, _, _ = parse_pbxproj(SYNTHETIC_PBXPROJ)
        app = next(t for t in targets if t.name == "MyApp")
        self.assertEqual(app.entitlements_file, "MyApp/MyApp.entitlements")

    def test_info_plist_file(self):
        from boss.intelligence.xcode import parse_pbxproj

        targets, _, _ = parse_pbxproj(SYNTHETIC_PBXPROJ)
        app = next(t for t in targets if t.name == "MyApp")
        self.assertEqual(app.info_plist_file, "MyApp/Info.plist")

    def test_build_configurations(self):
        from boss.intelligence.xcode import parse_pbxproj

        targets, project_configs, _ = parse_pbxproj(SYNTHETIC_PBXPROJ)
        self.assertIn("Debug", project_configs)
        self.assertIn("Release", project_configs)

        app = next(t for t in targets if t.name == "MyApp")
        self.assertIn("Debug", app.build_configurations)
        self.assertIn("Release", app.build_configurations)

    def test_empty_input(self):
        from boss.intelligence.xcode import parse_pbxproj

        targets, configs, errors = parse_pbxproj("")
        self.assertEqual(targets, [])
        self.assertEqual(configs, [])
        self.assertTrue(len(errors) > 0)

    def test_garbage_input(self):
        from boss.intelligence.xcode import parse_pbxproj

        targets, configs, errors = parse_pbxproj("not a pbxproj {{{")
        self.assertEqual(targets, [])


# ── Scheme parsing tests ───────────────────────────────────────────


class TestSchemeParsing(unittest.TestCase):
    """Parse synthetic xcscheme XML."""

    def test_parse_scheme(self):
        from boss.intelligence.xcode import parse_xcscheme

        scheme = parse_xcscheme(SYNTHETIC_XCSCHEME, "MyApp")
        self.assertIsNotNone(scheme)
        self.assertEqual(scheme.name, "MyApp")
        self.assertIn("MyApp", scheme.build_targets)
        self.assertIn("MyAppTests", scheme.test_targets)
        self.assertEqual(scheme.launch_target, "MyApp")

    def test_parse_invalid_xml(self):
        from boss.intelligence.xcode import parse_xcscheme

        result = parse_xcscheme("not xml <><>", "bad")
        self.assertIsNone(result)

    def test_scheme_to_dict(self):
        from boss.intelligence.xcode import parse_xcscheme

        scheme = parse_xcscheme(SYNTHETIC_XCSCHEME, "MyApp")
        d = scheme.to_dict()
        self.assertEqual(d["name"], "MyApp")
        self.assertIsInstance(d["build_targets"], list)
        self.assertIsInstance(d["test_targets"], list)


# ── Info.plist tests ────────────────────────────────────────────────


class TestInfoPlist(unittest.TestCase):
    """Read and extract fields from Info.plist."""

    def test_read_binary_plist(self):
        from boss.intelligence.xcode import extract_plist_summary, read_info_plist

        with tempfile.TemporaryDirectory() as td:
            plist_path = Path(td) / "Info.plist"
            plist_data = {
                "CFBundleIdentifier": "com.example.test",
                "CFBundleShortVersionString": "2.1",
                "CFBundleVersion": "42",
                "MinimumOSVersion": "17.0",
                "UIDeviceFamily": [1],
            }
            with plist_path.open("wb") as f:
                plistlib.dump(plist_data, f)

            loaded = read_info_plist(plist_path)
            self.assertEqual(loaded["CFBundleIdentifier"], "com.example.test")

            summary = extract_plist_summary(loaded)
            self.assertIn("CFBundleShortVersionString", summary)
            self.assertEqual(summary["MinimumOSVersion"], "17.0")

    def test_read_xml_plist(self):
        from boss.intelligence.xcode import read_info_plist

        with tempfile.TemporaryDirectory() as td:
            plist_path = Path(td) / "Info.plist"
            plist_data = {"CFBundleName": "TestApp", "CFBundleVersion": "1"}
            with plist_path.open("wb") as f:
                plistlib.dump(plist_data, f, fmt=plistlib.FMT_XML)

            loaded = read_info_plist(plist_path)
            self.assertEqual(loaded["CFBundleName"], "TestApp")

    def test_read_nonexistent_plist(self):
        from boss.intelligence.xcode import read_info_plist

        result = read_info_plist(Path("/nonexistent/Info.plist"))
        self.assertEqual(result, {})


# ── Entitlements tests ──────────────────────────────────────────────


class TestEntitlements(unittest.TestCase):
    def test_summarize_entitlements(self):
        from boss.intelligence.xcode import summarize_entitlements

        ent = {
            "aps-environment": "development",
            "com.apple.developer.associated-domains": ["applinks:example.com"],
            "keychain-access-groups": ["$(AppIdentifierPrefix)com.example.app"],
        }
        caps = summarize_entitlements(ent)
        self.assertIn("Push Notifications", caps)
        self.assertIn("Associated Domains", caps)
        self.assertIn("Keychain Sharing", caps)

    def test_empty_entitlements(self):
        from boss.intelligence.xcode import summarize_entitlements

        self.assertEqual(summarize_entitlements({}), [])


# ── Full project inspection tests ──────────────────────────────────


class TestInspectXcodeProject(unittest.TestCase):
    """End-to-end inspection of a synthetic Xcode project."""

    def test_inspect_synthetic_project(self):
        from boss.intelligence.xcode import inspect_xcode_project

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _create_synthetic_xcode_project(root)

            info = inspect_xcode_project(root)

            self.assertEqual(info.xcodeproj_path, "MyApp.xcodeproj")
            self.assertEqual(len(info.targets), 3)
            self.assertIsNotNone(info.likely_app_target)
            self.assertEqual(info.likely_app_target.name, "MyApp")
            self.assertEqual(info.likely_app_target.bundle_identifier, "com.example.myapp")
            self.assertEqual(len(info.test_targets), 2)

    def test_inspect_schemes(self):
        from boss.intelligence.xcode import inspect_xcode_project

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _create_synthetic_xcode_project(root)

            info = inspect_xcode_project(root)
            self.assertEqual(len(info.schemes), 1)
            self.assertEqual(info.schemes[0].name, "MyApp")
            self.assertIn("MyApp", info.schemes[0].build_targets)

    def test_inspect_info_plists(self):
        from boss.intelligence.xcode import inspect_xcode_project

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _create_synthetic_xcode_project(root)

            info = inspect_xcode_project(root)
            self.assertTrue(any("Info.plist" in p for p in info.info_plists))

    def test_inspect_entitlements(self):
        from boss.intelligence.xcode import inspect_xcode_project

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _create_synthetic_xcode_project(root)

            info = inspect_xcode_project(root)
            self.assertTrue(any(".entitlements" in p for p in info.entitlements_files))

    def test_inspect_empty_directory(self):
        from boss.intelligence.xcode import inspect_xcode_project

        with tempfile.TemporaryDirectory() as td:
            info = inspect_xcode_project(td)
            self.assertEqual(info.targets, [])
            self.assertEqual(info.schemes, [])
            self.assertIsNone(info.likely_app_target)

    def test_inspect_spm_only(self):
        from boss.intelligence.xcode import inspect_xcode_project

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Package.swift").write_text("// swift-tools-version: 5.9\n", encoding="utf-8")
            info = inspect_xcode_project(root)
            self.assertTrue(info.has_swift_package)
            self.assertEqual(info.targets, [])

    def test_summary_output(self):
        from boss.intelligence.xcode import inspect_xcode_project

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _create_synthetic_xcode_project(root)

            info = inspect_xcode_project(root)
            summary = info.summary()

            self.assertIn("MyApp", summary)
            self.assertIn("com.example.myapp", summary)
            self.assertIn("signing=automatic", summary)
            self.assertIn("Schemes", summary)

    def test_to_dict_output(self):
        from boss.intelligence.xcode import inspect_xcode_project

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _create_synthetic_xcode_project(root)

            info = inspect_xcode_project(root)
            d = info.to_dict()

            self.assertIn("targets", d)
            self.assertIn("schemes", d)
            self.assertIn("likely_app_target", d)
            self.assertIsNotNone(d["likely_app_target"])
            self.assertEqual(d["likely_app_target"]["name"], "MyApp")

    def test_build_configurations_in_dict(self):
        from boss.intelligence.xcode import inspect_xcode_project

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _create_synthetic_xcode_project(root)

            info = inspect_xcode_project(root)
            self.assertIn("Debug", info.build_configurations)
            self.assertIn("Release", info.build_configurations)


# ── Scanner integration tests ──────────────────────────────────────


class TestScannerAppleIntegration(unittest.TestCase):
    """Verify the memory scanner correctly handles Apple project structure."""

    def test_xcodeproj_not_skipped(self):
        from boss.memory.scanner import _should_skip_dir

        self.assertFalse(_should_skip_dir("MyApp.xcodeproj"))

    def test_xcworkspace_not_skipped(self):
        from boss.memory.scanner import _should_skip_dir

        self.assertFalse(_should_skip_dir("MyApp.xcworkspace"))

    def test_derived_data_still_skipped(self):
        from boss.memory.scanner import _should_skip_dir

        self.assertTrue(_should_skip_dir("DerivedData"))
        self.assertTrue(_should_skip_dir("Pods"))

    def test_xcode_internal_dirs_skipped(self):
        from boss.memory.scanner import _should_skip_dir

        self.assertTrue(_should_skip_dir("xcuserdata"))
        self.assertTrue(_should_skip_dir("xcshareddata"))

    def test_apple_project_detection(self):
        from boss.memory.scanner import _detect_project_type, _is_project_root

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "MyApp.xcodeproj").mkdir()

            self.assertTrue(_is_project_root(root))
            self.assertEqual(_detect_project_type(root), "swift")

    def test_collect_apple_project_files(self):
        from boss.memory.scanner import _collect_apple_project_files

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _create_synthetic_xcode_project(root)

            files = _collect_apple_project_files(root)
            names = {f.name for f in files}

            self.assertIn("project.pbxproj", names)
            self.assertIn("MyApp.xcscheme", names)

    def test_stack_detection_ios(self):
        from boss.memory.scanner import _infer_stack

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _create_synthetic_xcode_project(root)

            relative_paths = [
                Path("MyApp.xcodeproj/project.pbxproj"),
                Path("MyApp/ContentView.swift"),
                Path("MyApp/Info.plist"),
            ]
            stack = _infer_stack(root, "swift", relative_paths, [])

            self.assertIn("Swift", stack)
            self.assertIn("Xcode", stack)
            # The synthetic pbxproj has SDKROOT = iphoneos in build settings
            # but the stack inferrer reads the top of the file; check for iOS presence
            # if the pbxproj contains iphoneos hints
            self.assertIn("iOS", stack)


# ── Nested Apple-project discovery tests ───────────────────────────


def _create_nested_xcode_project(root: Path, subdir: str = "ios") -> Path:
    """Create a synthetic .xcodeproj inside *root/subdir/* (React Native style)."""
    nested = root / subdir
    nested.mkdir(parents=True, exist_ok=True)
    _create_synthetic_xcode_project(nested)
    return root


class TestNestedProjectDiscovery(unittest.TestCase):
    """Ensure discovery works when the .xcodeproj lives one level down."""

    def test_find_xcodeproj_in_ios_subdir(self):
        from boss.intelligence.xcode import _find_xcodeproj

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _create_nested_xcode_project(root, "ios")

            proj = _find_xcodeproj(root)
            self.assertIsNotNone(proj, "should find .xcodeproj inside ios/")
            self.assertEqual(proj.name, "MyApp.xcodeproj")
            self.assertEqual(proj.parent.name, "ios")

    def test_find_xcodeproj_in_macos_subdir(self):
        from boss.intelligence.xcode import _find_xcodeproj

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _create_nested_xcode_project(root, "macos")

            proj = _find_xcodeproj(root)
            self.assertIsNotNone(proj, "should find .xcodeproj inside macos/")
            self.assertEqual(proj.parent.name, "macos")

    def test_find_xcodeproj_in_app_subdir(self):
        from boss.intelligence.xcode import _find_xcodeproj

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _create_nested_xcode_project(root, "app")

            proj = _find_xcodeproj(root)
            self.assertIsNotNone(proj, "should find .xcodeproj inside app/")

    def test_root_project_preferred_over_nested(self):
        """If both root and nested exist, root should be found first."""
        from boss.intelligence.xcode import _find_xcodeproj

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Root-level project
            _create_synthetic_xcode_project(root)
            # Also put one in ios/
            _create_nested_xcode_project(root, "ios")

            proj = _find_xcodeproj(root)
            self.assertIsNotNone(proj)
            # Root-level should win
            self.assertEqual(proj.parent, root)

    def test_find_xcworkspace_in_ios_subdir(self):
        from boss.intelligence.xcode import _find_xcworkspace

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ios = root / "ios"
            ios.mkdir()
            ws = ios / "MyApp.xcworkspace"
            ws.mkdir()
            (ws / "contents.xcworkspacedata").write_text("<Workspace/>", encoding="utf-8")

            found = _find_xcworkspace(root)
            self.assertIsNotNone(found, "should find .xcworkspace inside ios/")
            self.assertEqual(found.parent.name, "ios")

    def test_inspect_project_nested_ios(self):
        """Full inspect_xcode_project through a nested ios/ layout."""
        from boss.intelligence.xcode import inspect_xcode_project

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _create_nested_xcode_project(root, "ios")

            info = inspect_xcode_project(root)

            self.assertIsNotNone(info.xcodeproj_path)
            self.assertIn("ios", info.xcodeproj_path)
            self.assertEqual(len(info.targets), 3)
            self.assertIsNotNone(info.likely_app_target)
            self.assertEqual(info.likely_app_target.name, "MyApp")
            self.assertEqual(len(info.schemes), 1)

    def test_inspect_project_nested_with_package_json(self):
        """React Native style: package.json at root, .xcodeproj inside ios/."""
        from boss.intelligence.xcode import inspect_xcode_project

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "package.json").write_text('{"name":"RNApp"}', encoding="utf-8")
            (root / "App.tsx").write_text("export default App;", encoding="utf-8")
            _create_nested_xcode_project(root, "ios")

            info = inspect_xcode_project(root)
            self.assertIsNotNone(info.xcodeproj_path)
            self.assertEqual(len(info.targets), 3)

    def test_not_found_in_arbitrary_deep_subdir(self):
        """Projects more than one level deep should NOT be discovered."""
        from boss.intelligence.xcode import _find_xcodeproj

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            deep = root / "some" / "deep" / "path"
            deep.mkdir(parents=True)
            (deep / "MyApp.xcodeproj").mkdir()

            proj = _find_xcodeproj(root)
            self.assertIsNone(proj, "should not find projects more than 1 level deep")

    def test_unknown_subdir_not_searched(self):
        """Subdirs not in the known nested-dirs set should be ignored."""
        from boss.intelligence.xcode import _find_xcodeproj

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            weird = root / "randomfolder"
            weird.mkdir()
            (weird / "MyApp.xcodeproj").mkdir()

            proj = _find_xcodeproj(root)
            self.assertIsNone(proj, "should not search arbitrary subdirectories")


class TestScannerNestedAppleIntegration(unittest.TestCase):
    """Scanner correctly attaches metadata for nested Apple project layouts."""

    def test_attach_metadata_nested_ios(self):
        from boss.memory.scanner import _attach_apple_project_metadata

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _create_nested_xcode_project(root, "ios")

            metadata: dict = {}
            _attach_apple_project_metadata(root, metadata)

            self.assertIn("apple_project", metadata)
            ap = metadata["apple_project"]
            self.assertIn("ios", ap.get("xcodeproj_path", ""))
            self.assertTrue(len(ap.get("targets", [])) > 0)

    def test_attach_metadata_empty_project(self):
        from boss.memory.scanner import _attach_apple_project_metadata

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            metadata: dict = {}
            _attach_apple_project_metadata(root, metadata)

            # Should not attach anything for a plain directory
            self.assertNotIn("apple_project", metadata)


if __name__ == "__main__":
    unittest.main()
