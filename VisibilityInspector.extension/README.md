# Visibility Inspector — Why Hidden?

A read-only pyRevit MVP for diagnosing why an element is missing from a Revit view.

## Supported workflow

1. Open a view where the element **is visible**.
2. Run **Visibility Inspector > QA > Why Hidden?**
3. Choose:
   - **Pick HOST element**, or
   - **Pick LINKED element**
4. Pick the element.
5. Choose the **target view** where the element is missing.
6. Read the diagnostic report in the pyRevit output window.

## Checks included

### Host elements
- Permanent Hide in View
- Category visibility
- Workset visibility
- Hidden Parameter / Selection filters
- Temporary Hide/Isolate
- View-specific / Owner View
- Phase information/status
- Design Option warning
- Crop Region
- 3D Section Box
- Plan View Range
- Target View Template / Discipline / Detail Level information
- View element collector sanity check

### Linked elements
- Host RevitLinkInstance permanent hide
- Link instance category / workset / filters
- Host crop / section box / view range using link transform
- Host category visibility for the linked element category
- RevitLinkGraphicsSettings when exposed by the Revit version/API
- Linked View ID and linked-view checks when a Linked View controls display
- Linked element workset / phase / design option information
- Warning when the picked object is itself a nested RevitLinkInstance

## Important limitation

Revit does not provide a single API that returns the exact reason an element is invisible.
Some Revit Link custom graphics settings and some family/category-specific display rules cannot
be evaluated with certainty from one call. The tool therefore uses:

- **FAIL** = API-confirmed blocker
- **WARN** = likely/possible blocker requiring review
- **OK** = check passed
- **INFO** = context / diagnostic information

The tool is intentionally **read-only** in this MVP.

## Installation

Copy the whole folder:

`VisibilityInspector.extension`

into your pyRevit extensions folder, for example:

`%APPDATA%\pyRevit\Extensions\`

Then reload pyRevit or restart Revit.

## Revit / pyRevit

The script is written conservatively for pyRevit / IronPython-style compatibility and targets
common Revit 2024–2026 API capabilities. Revit Link graphics introspection is guarded so that
missing API members do not crash the tool.
