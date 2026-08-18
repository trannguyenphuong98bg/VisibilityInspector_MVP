# -*- coding: utf-8 -*-
"""
Why Hidden? - pyRevit visibility diagnostic tool
Read-only MVP for Revit 2024-2026.

Workflow:
1) Run from any view where the target element can be selected.
2) Pick a host or linked element.
3) Choose the target view where the element is missing.
4) Review the diagnostic report.

Notes:
- The Revit API exposes many, but not all, visibility rules.
- The tool only marks FAIL when a rule can be confirmed from API data.
- WARN means the setting may affect visibility and should be reviewed.
"""

from pyrevit import revit, DB, UI, forms, script

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()

try:
    unicode
except NameError:
    unicode = str


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def id_value(eid):
    if eid is None:
        return None
    try:
        return eid.Value
    except Exception:
        try:
            return eid.IntegerValue
        except Exception:
            return None


def is_invalid_id(eid):
    if eid is None:
        return True
    val = id_value(eid)
    return val is None or val < 0


def safe_name(elem, fallback="<unnamed>"):
    if elem is None:
        return fallback
    try:
        return elem.Name
    except Exception:
        try:
            p = elem.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_NAME)
            return p.AsString() if p else fallback
        except Exception:
            return fallback


def category_name(elem):
    try:
        return elem.Category.Name if elem.Category else "<no category>"
    except Exception:
        return "<no category>"


def doc_title(d):
    try:
        return d.Title
    except Exception:
        return "<document>"


def get_element_type_name(d, elem):
    try:
        tid = elem.GetTypeId()
        if not is_invalid_id(tid):
            t = d.GetElement(tid)
            return safe_name(t, "<type>")
    except Exception:
        pass
    return "<type unavailable>"


def md_escape(value):
    if value is None:
        return ""
    s = unicode(value)
    return s.replace("|", "\\|").replace("\n", " ")


class Report(object):
    def __init__(self):
        self.rows = []
        self.fail_count = 0
        self.warn_count = 0

    def add(self, status, check, finding, action=""):
        status = status.upper()
        if status == "FAIL":
            self.fail_count += 1
        elif status == "WARN":
            self.warn_count += 1
        self.rows.append((status, check, finding, action))

    def ok(self, check, finding, action=""):
        self.add("OK", check, finding, action)

    def fail(self, check, finding, action=""):
        self.add("FAIL", check, finding, action)

    def warn(self, check, finding, action=""):
        self.add("WARN", check, finding, action)

    def info(self, check, finding, action=""):
        self.add("INFO", check, finding, action)

    def print(self):
        output.print_md("## Visibility Diagnostic Report")
        output.print_md("| Status | Check | Finding | Suggested action |")
        output.print_md("|---|---|---|---|")
        for status, check, finding, action in self.rows:
            if status == "FAIL":
                badge = "🔴 **FAIL**"
            elif status == "WARN":
                badge = "🟠 **WARN**"
            elif status == "OK":
                badge = "🟢 OK"
            else:
                badge = "🔵 INFO"
            output.print_md("| {} | {} | {} | {} |".format(
                badge, md_escape(check), md_escape(finding), md_escape(action)
            ))

        output.print_md("")
        if self.fail_count:
            output.print_md("### Result: {} confirmed visibility issue(s) found".format(self.fail_count))
        elif self.warn_count:
            output.print_md("### Result: no confirmed blocker found; review {} warning(s)".format(self.warn_count))
        else:
            output.print_md("### Result: no blocker detected by the checks available in this MVP")


def get_graphical_views(d):
    views = []
    for v in DB.FilteredElementCollector(d).OfClass(DB.View):
        try:
            if v.IsTemplate:
                continue
            # Avoid enum members that vary across Revit versions.
            if isinstance(v, DB.ViewSheet) or isinstance(v, DB.ViewSchedule):
                continue
            try:
                if v.ViewType == DB.ViewType.Internal:
                    continue
            except Exception:
                pass
            views.append(v)
        except Exception:
            continue
    return views


def choose_target_view():
    active = doc.ActiveView
    first = "Current active view: {} [{}]".format(active.Name, active.ViewType)
    mode = forms.CommandSwitchWindow.show(
        [first, "Choose another view..."],
        message="Select the target view where the element is missing:"
    )
    if not mode:
        script.exit()
    if mode == first:
        return active

    views = get_graphical_views(doc)
    mapping = {}
    labels = []
    for v in views:
        label = "{}  |  {}  |  ID {}".format(v.Name, v.ViewType, id_value(v.Id))
        # Duplicate names can occur, but ID makes the label unique.
        mapping[label] = v
        labels.append(label)
    labels.sort(key=lambda x: x.lower())
    picked = forms.SelectFromList.show(
        labels,
        title="Choose Target View",
        multiselect=False,
        button_name="Diagnose"
    )
    if not picked:
        script.exit()
    return mapping[picked]


def bbox_corners(bb):
    if bb is None:
        return []
    mn, mx = bb.Min, bb.Max
    pts = []
    for x in (mn.X, mx.X):
        for y in (mn.Y, mx.Y):
            for z in (mn.Z, mx.Z):
                pts.append(XYZ(x, y, z))
    return pts


# Alias to make corner creation concise.
XYZ = DB.XYZ


def transform_points(points, transform):
    if transform is None:
        return points
    result = []
    for p in points:
        try:
            result.append(transform.OfPoint(p))
        except Exception:
            result.append(p)
    return result


def get_world_bbox_points(elem, link_transform=None):
    try:
        bb = elem.get_BoundingBox(None)
    except Exception:
        bb = None
    if bb is None:
        return []
    pts = bbox_corners(bb)
    if link_transform is not None:
        pts = transform_points(pts, link_transform)
    return pts


def extents_in_transform(points, inverse_transform=None):
    if not points:
        return None
    vals = []
    for p in points:
        try:
            q = inverse_transform.OfPoint(p) if inverse_transform else p
        except Exception:
            q = p
        vals.append(q)
    return (
        min(p.X for p in vals), max(p.X for p in vals),
        min(p.Y for p in vals), max(p.Y for p in vals),
        min(p.Z for p in vals), max(p.Z for p in vals),
    )


def aabb_overlap_2d(a, b):
    return not (
        a[1] < b[0] or a[0] > b[1] or
        a[3] < b[2] or a[2] > b[3]
    )


def aabb_overlap_3d(a, b):
    return not (
        a[1] < b[0] or a[0] > b[1] or
        a[3] < b[2] or a[2] > b[3] or
        a[5] < b[4] or a[4] > b[5]
    )


def get_workset_name(d, wsid):
    try:
        wt = d.GetWorksetTable()
        ws = wt.GetWorkset(wsid)
        return ws.Name if ws else "ID {}".format(id_value(wsid))
    except Exception:
        return "ID {}".format(id_value(wsid))


def check_explicit_hide(report, view, elem, label):
    try:
        if elem.IsHidden(view):
            report.fail(
                label + " / Hide in View",
                "Element is permanently hidden in this view.",
                "Unhide in View > Elements."
            )
        else:
            report.ok(label + " / Hide in View", "Element is not permanently hidden.")
    except Exception as ex:
        report.info(label + " / Hide in View", "API check unavailable: {}".format(ex))


def check_category(report, view, elem, label):
    cat = None
    try:
        cat = elem.Category
    except Exception:
        cat = None
    if cat is None:
        report.info(label + " / Category", "Element has no category.")
        return
    try:
        if view.GetCategoryHidden(cat.Id):
            report.fail(
                label + " / Category",
                "Category '{}' is hidden in target view.".format(cat.Name),
                "Open Visibility/Graphics and enable the category."
            )
        else:
            report.ok(label + " / Category", "Category '{}' is visible.".format(cat.Name))
    except Exception as ex:
        report.info(label + " / Category", "Could not query category visibility: {}".format(ex))


def check_workset(report, view, elem, elem_doc, label):
    try:
        wsid = elem.WorksetId
    except Exception:
        return
    if is_invalid_id(wsid):
        return
    wsname = get_workset_name(elem_doc, wsid)

    # Only a view in the same document can directly evaluate that workset.
    if elem_doc.Equals(view.Document):
        try:
            visible = view.IsWorksetVisible(wsid)
            if not visible:
                report.fail(
                    label + " / Workset",
                    "Workset '{}' is not visible in target view.".format(wsname),
                    "Set the workset to Visible/Use Global and make sure it is open."
                )
            else:
                report.ok(label + " / Workset", "Workset '{}' is visible.".format(wsname))
        except Exception as ex:
            report.info(label + " / Workset", "Could not evaluate workset: {}".format(ex))
    else:
        report.info(
            label + " / Workset",
            "Element belongs to linked-model workset '{}'. Host view cannot directly evaluate this linked workset unless a linked view controls display.".format(wsname)
        )


def filter_matches_element(filter_elem, d, elem):
    try:
        if isinstance(filter_elem, DB.ParameterFilterElement):
            ef = filter_elem.GetElementFilter()
            return ef.PassesFilter(d, elem.Id)
    except Exception:
        pass
    try:
        if isinstance(filter_elem, DB.SelectionFilterElement):
            ids = filter_elem.GetElementIds()
            return elem.Id in ids
    except Exception:
        pass
    return None


def check_filters(report, view, elem, elem_doc, label):
    if not elem_doc.Equals(view.Document):
        report.info(
            label + " / View Filters",
            "Host view filters cannot be reliably tested against an element ID from another document."
        )
        return
    try:
        filter_ids = list(view.GetFilters())
    except Exception:
        return
    if not filter_ids:
        report.ok(label + " / View Filters", "No view filters are assigned.")
        return

    matched_hidden = []
    unknown_hidden = []
    for fid in filter_ids:
        try:
            visible = view.GetFilterVisibility(fid)
        except Exception:
            continue
        if visible:
            continue
        fe = elem_doc.GetElement(fid)
        match = filter_matches_element(fe, elem_doc, elem)
        if match is True:
            matched_hidden.append(safe_name(fe, "Filter {}".format(id_value(fid))))
        elif match is None:
            unknown_hidden.append(safe_name(fe, "Filter {}".format(id_value(fid))))

    if matched_hidden:
        report.fail(
            label + " / View Filters",
            "Element matches hidden filter(s): {}.".format(", ".join(matched_hidden)),
            "Enable those filters or change their rules."
        )
    elif unknown_hidden:
        report.warn(
            label + " / View Filters",
            "Some hidden filters could not be evaluated: {}.".format(", ".join(unknown_hidden)),
            "Review these filters manually."
        )
    else:
        report.ok(label + " / View Filters", "No hidden filter was found matching the element.")


def check_owner_view(report, view, elem, label):
    try:
        if elem.ViewSpecific:
            owner_id = elem.OwnerViewId
            if owner_id != view.Id:
                owner = elem.Document.GetElement(owner_id)
                report.fail(
                    label + " / Owner View",
                    "Element is view-specific and belongs to '{}', not target view '{}'.".format(
                        safe_name(owner, "view ID {}".format(id_value(owner_id))), view.Name
                    ),
                    "Use the element in its owner view or recreate/copy it in the target view."
                )
            else:
                report.ok(label + " / Owner View", "View-specific element belongs to target view.")
    except Exception:
        pass


def check_temporary_hide(report, view, elem, label):
    try:
        active = view.IsTemporaryHideIsolateActive()
    except Exception:
        active = False
    if not active:
        report.ok(label + " / Temporary Hide-Isolate", "Temporary Hide/Isolate is not active.")
        return

    try:
        visible = view.IsElementVisibleInTemporaryViewMode(
            DB.TemporaryViewMode.TemporaryHideIsolate, elem.Id
        )
        if not visible:
            report.fail(
                label + " / Temporary Hide-Isolate",
                "Element is excluded by Temporary Hide/Isolate.",
                "Reset Temporary Hide/Isolate."
            )
        else:
            report.ok(label + " / Temporary Hide-Isolate", "Element remains visible in temporary mode.")
    except Exception:
        report.warn(
            label + " / Temporary Hide-Isolate",
            "Temporary Hide/Isolate is active, but per-element visibility could not be confirmed.",
            "Reset Temporary Hide/Isolate to rule it out."
        )


def check_crop(report, view, world_points, label):
    if not world_points:
        report.info(label + " / Crop Region", "No bounding box was available for crop testing.")
        return
    try:
        active = view.CropBoxActive
    except Exception:
        active = False
    if not active:
        report.ok(label + " / Crop Region", "Crop region is not active.")
        return

    try:
        cb = view.CropBox
        inv = cb.Transform.Inverse
        e = extents_in_transform(world_points, inv)
        c = (cb.Min.X, cb.Max.X, cb.Min.Y, cb.Max.Y, cb.Min.Z, cb.Max.Z)
        if aabb_overlap_2d(e, c):
            report.ok(label + " / Crop Region", "Element bounding box overlaps the target view crop region.")
        else:
            report.fail(
                label + " / Crop Region",
                "Element bounding box is outside the target view crop region.",
                "Expand/move the crop region or verify the element location."
            )
    except Exception as ex:
        report.info(label + " / Crop Region", "Crop test unavailable: {}".format(ex))


def check_section_box(report, view, world_points, label):
    if not isinstance(view, DB.View3D):
        return
    try:
        active = view.IsSectionBoxActive
    except Exception:
        active = False
    if not active:
        report.ok(label + " / Section Box", "3D section box is not active.")
        return
    if not world_points:
        report.info(label + " / Section Box", "No bounding box available.")
        return
    try:
        sb = view.GetSectionBox()
        inv = sb.Transform.Inverse
        e = extents_in_transform(world_points, inv)
        s = (sb.Min.X, sb.Max.X, sb.Min.Y, sb.Max.Y, sb.Min.Z, sb.Max.Z)
        if aabb_overlap_3d(e, s):
            report.ok(label + " / Section Box", "Element bounding box overlaps the 3D section box.")
        else:
            report.fail(
                label + " / Section Box",
                "Element bounding box is outside the 3D section box.",
                "Expand/move the section box."
            )
    except Exception as ex:
        report.info(label + " / Section Box", "Section box test unavailable: {}".format(ex))


def plane_elevation(d, vr, plane):
    try:
        lid = vr.GetLevelId(plane)
        offset = vr.GetOffset(plane)
        if is_invalid_id(lid):
            return None
        level = d.GetElement(lid)
        if level is None:
            return None
        return level.Elevation + offset
    except Exception:
        return None


def check_view_range(report, view, world_points, label):
    if not isinstance(view, DB.ViewPlan):
        return
    if not world_points:
        report.info(label + " / View Range", "No bounding box available.")
        return
    try:
        vr = view.GetViewRange()
    except Exception as ex:
        report.info(label + " / View Range", "View range unavailable: {}".format(ex))
        return

    top = plane_elevation(view.Document, vr, DB.PlanViewPlane.TopClipPlane)
    bottom = plane_elevation(view.Document, vr, DB.PlanViewPlane.BottomClipPlane)
    depth = plane_elevation(view.Document, vr, DB.PlanViewPlane.ViewDepthPlane)
    zmin = min(p.Z for p in world_points)
    zmax = max(p.Z for p in world_points)

    if top is None or bottom is None:
        report.warn(
            label + " / View Range",
            "Could not resolve top/bottom plane elevations. Element Z range: {:.3f} to {:.3f} ft.".format(zmin, zmax),
            "Review View Range manually."
        )
        return

    lo = min(bottom, top)
    hi = max(bottom, top)
    primary_overlap = not (zmax < lo or zmin > hi)

    if primary_overlap:
        report.ok(
            label + " / View Range",
            "Element Z range ({:.3f}–{:.3f} ft) overlaps primary range ({:.3f}–{:.3f} ft).".format(
                zmin, zmax, lo, hi
            )
        )
    else:
        # The view depth can still make some categories visible; mark WARN, not hard FAIL.
        if depth is not None:
            full_lo = min(lo, depth)
            full_hi = max(hi, depth)
            if not (zmax < full_lo or zmin > full_hi):
                report.warn(
                    label + " / View Range",
                    "Element is outside the primary range but overlaps View Depth.",
                    "Visibility depends on category and plan-view rules; review View Range."
                )
                return
        report.fail(
            label + " / View Range",
            "Element Z range ({:.3f}–{:.3f} ft) is outside Top/Bottom/View Depth extents.".format(zmin, zmax),
            "Adjust View Range / Plan Region or verify element elevation."
        )


def get_phase_name(d, eid):
    if is_invalid_id(eid):
        return "<none>"
    try:
        return safe_name(d.GetElement(eid), "ID {}".format(id_value(eid)))
    except Exception:
        return "ID {}".format(id_value(eid))


def check_phase(report, view, elem, elem_doc, label):
    view_phase_id = None
    try:
        p = view.get_Parameter(DB.BuiltInParameter.VIEW_PHASE)
        if p:
            view_phase_id = p.AsElementId()
    except Exception:
        pass

    if view_phase_id is None or is_invalid_id(view_phase_id):
        return

    view_phase_name = get_phase_name(view.Document, view_phase_id)

    if elem_doc.Equals(view.Document):
        try:
            status = elem.GetPhaseStatus(view_phase_id)
            status_text = unicode(status)
            if status_text.lower() in ("none", "invalid"):
                report.fail(
                    label + " / Phase",
                    "Element phase status in view phase '{}' is '{}'.".format(view_phase_name, status_text),
                    "Review Created/Demolished Phase and the view Phase/Phase Filter."
                )
            else:
                report.info(
                    label + " / Phase",
                    "View phase: '{}'; element phase status: '{}'.".format(view_phase_name, status_text)
                )
        except Exception:
            report.info(label + " / Phase", "Target view phase is '{}'.".format(view_phase_name))
    else:
        try:
            created = get_phase_name(elem_doc, elem.CreatedPhaseId)
            demolished = get_phase_name(elem_doc, elem.DemolishedPhaseId)
            report.info(
                label + " / Phase",
                "Host view phase: '{}'; linked element Created: '{}', Demolished: '{}'.".format(
                    view_phase_name, created, demolished
                )
            )
        except Exception:
            report.info(label + " / Phase", "Host view phase is '{}'.".format(view_phase_name))

    try:
        pf = view.get_Parameter(DB.BuiltInParameter.VIEW_PHASE_FILTER)
        if pf:
            pfid = pf.AsElementId()
            report.info(label + " / Phase Filter", "Target view Phase Filter: '{}'.".format(
                get_phase_name(view.Document, pfid)
            ))
    except Exception:
        pass


def check_design_option(report, view, elem, label):
    try:
        opt = elem.DesignOption
    except Exception:
        opt = None
    if opt is None:
        return
    report.warn(
        label + " / Design Option",
        "Element belongs to design option '{}'.".format(safe_name(opt)),
        "Verify the target view's Design Option setting."
    )


def check_view_discipline(report, view):
    try:
        report.info("Target View / Discipline", unicode(view.Discipline))
    except Exception:
        pass
    try:
        report.info("Target View / Detail Level", unicode(view.DetailLevel))
    except Exception:
        pass
    try:
        if not is_invalid_id(view.ViewTemplateId):
            t = view.Document.GetElement(view.ViewTemplateId)
            report.info(
                "Target View / View Template",
                "'{}' controls some visibility settings.".format(safe_name(t))
            )
    except Exception:
        pass


def check_link_instance(report, target_view, link_inst):
    check_explicit_hide(report, target_view, link_inst, "Host / Link Instance")
    check_category(report, target_view, link_inst, "Host / Link Instance")
    check_workset(report, target_view, link_inst, doc, "Host / Link Instance")
    check_filters(report, target_view, link_inst, doc, "Host / Link Instance")
    check_temporary_hide(report, target_view, link_inst, "Host / Link Instance")


def get_link_graphics_settings(view, link_inst):
    try:
        if hasattr(view, "GetLinkOverrides"):
            return view.GetLinkOverrides(link_inst.Id)
    except Exception:
        return None
    return None


def get_linked_view_from_settings(settings, link_doc):
    if settings is None:
        return None
    try:
        lvid = settings.LinkedViewId
        if not is_invalid_id(lvid):
            return link_doc.GetElement(lvid)
    except Exception:
        pass
    return None


def report_link_settings(report, target_view, link_inst, link_doc):
    settings = get_link_graphics_settings(target_view, link_inst)
    if settings is None:
        report.warn(
            "Host / Link Display",
            "Detailed Revit link graphics settings are not exposed by this Revit/API context.",
            "Review Visibility/Graphics > Revit Links > Display Settings."
        )
        return None

    try:
        report.info("Host / Link Display", "Visibility type: '{}'.".format(settings.LinkVisibilityType))
    except Exception:
        report.info("Host / Link Display", "Link graphics overrides are available.")

    linked_view = get_linked_view_from_settings(settings, link_doc)
    if linked_view is not None:
        report.info(
            "Host / Linked View",
            "Link display references linked view '{}', ID {}.".format(linked_view.Name, id_value(linked_view.Id))
        )
    else:
        try:
            lvid = settings.LinkedViewId
            if is_invalid_id(lvid):
                report.info("Host / Linked View", "No linked view ID is assigned in link graphics settings.")
        except Exception:
            pass

    # Expose other settings dynamically as INFO without relying on one Revit version.
    interesting = [
        "ViewRange", "Phase", "PhaseFilter", "Discipline",
        "ObjectStyles", "NestedLinks", "ColorFill", "Underlay"
    ]
    for name in interesting:
        try:
            value = getattr(settings, name)
            report.info("Host / Link Setting / " + name, unicode(value))
        except Exception:
            pass
    return linked_view


def check_linked_view_rules(report, linked_view, linked_elem, link_doc):
    if linked_view is None:
        return

    check_explicit_hide(report, linked_view, linked_elem, "Linked View")
    check_category(report, linked_view, linked_elem, "Linked View")
    check_workset(report, linked_view, linked_elem, link_doc, "Linked View")
    check_filters(report, linked_view, linked_elem, link_doc, "Linked View")
    check_temporary_hide(report, linked_view, linked_elem, "Linked View")
    check_owner_view(report, linked_view, linked_elem, "Linked View")
    check_phase(report, linked_view, linked_elem, link_doc, "Linked View")
    check_design_option(report, linked_view, linked_elem, "Linked View")

    # Linked-view crop/range use linked-document coordinates.
    linked_points = get_world_bbox_points(linked_elem, None)
    check_crop(report, linked_view, linked_points, "Linked View")
    check_section_box(report, linked_view, linked_points, "Linked View")
    check_view_range(report, linked_view, linked_points, "Linked View")


def check_visibility_collector(report, view, elem):
    # A strong sanity check for host elements only.
    try:
        ids = DB.FilteredElementCollector(view.Document, view.Id).WhereElementIsNotElementType().ToElementIds()
        if elem.Id in ids:
            report.ok("Host / View Collector", "Element is present in the target view's element collector.")
        else:
            report.warn(
                "Host / View Collector",
                "Element is not present in the target view's element collector.",
                "One or more view-specific rules are excluding it."
            )
    except Exception:
        pass


# ----------------------------------------------------------------------
# Selection
# ----------------------------------------------------------------------

mode = forms.CommandSwitchWindow.show(
    ["Pick HOST element", "Pick LINKED element"],
    message="What kind of element do you want to diagnose?"
)
if not mode:
    script.exit()

link_inst = None
link_doc = None
link_transform = None

try:
    if mode == "Pick HOST element":
        ref = uidoc.Selection.PickObject(
            UI.Selection.ObjectType.Element,
            "Pick an element that is visible in this view"
        )
        elem = doc.GetElement(ref.ElementId)
        elem_doc = doc
        source_kind = "HOST"
    else:
        ref = uidoc.Selection.PickObject(
            UI.Selection.ObjectType.LinkedElement,
            "Pick an element inside a Revit Link"
        )
        link_inst = doc.GetElement(ref.ElementId)
        if not isinstance(link_inst, DB.RevitLinkInstance):
            forms.alert("The selected reference is not a RevitLinkInstance.", exitscript=True)
        link_doc = link_inst.GetLinkDocument()
        if link_doc is None:
            forms.alert("The selected Revit Link is unloaded or its document is unavailable.", exitscript=True)
        elem = link_doc.GetElement(ref.LinkedElementId)
        if elem is None:
            forms.alert("Could not resolve the linked element.", exitscript=True)
        elem_doc = link_doc
        source_kind = "LINKED"
        try:
            link_transform = link_inst.GetTotalTransform()
        except Exception:
            link_transform = link_inst.GetTransform()
except Exception:
    # Covers ESC / operation cancelled.
    script.exit()

target_view = choose_target_view()

# ----------------------------------------------------------------------
# Report header
# ----------------------------------------------------------------------

report = Report()

output.print_md("# Why Hidden?")
output.print_md("**Target view:** `{}` ({})  \n**Target view ID:** `{}`".format(
    target_view.Name, target_view.ViewType, id_value(target_view.Id)
))
output.print_md("**Source:** `{}`  \n**Document:** `{}`  \n**Category:** `{}`  \n**Element:** `{}`  \n**Type:** `{}`  \n**Element ID:** `{}`".format(
    source_kind,
    doc_title(elem_doc),
    category_name(elem),
    safe_name(elem, elem.GetType().Name),
    get_element_type_name(elem_doc, elem),
    id_value(elem.Id)
))
if link_inst is not None:
    output.print_md("**Host link instance:** `{}`  \n**Link instance ID:** `{}`".format(
        safe_name(link_inst, "Revit Link"), id_value(link_inst.Id)
    ))
output.print_md("---")

# ----------------------------------------------------------------------
# Diagnose
# ----------------------------------------------------------------------

check_view_discipline(report, target_view)

if source_kind == "HOST":
    check_explicit_hide(report, target_view, elem, "Host Element")
    check_category(report, target_view, elem, "Host Element")
    check_workset(report, target_view, elem, doc, "Host Element")
    check_filters(report, target_view, elem, doc, "Host Element")
    check_temporary_hide(report, target_view, elem, "Host Element")
    check_owner_view(report, target_view, elem, "Host Element")
    check_phase(report, target_view, elem, doc, "Host Element")
    check_design_option(report, target_view, elem, "Host Element")

    world_points = get_world_bbox_points(elem, None)
    check_crop(report, target_view, world_points, "Host Element")
    check_section_box(report, target_view, world_points, "Host Element")
    check_view_range(report, target_view, world_points, "Host Element")
    check_visibility_collector(report, target_view, elem)

else:
    # Layer 1: host controls over the RevitLinkInstance.
    check_link_instance(report, target_view, link_inst)

    # The selected element is in link coordinates; transform it into host coordinates
    # for host crop / section box / view range tests.
    host_points = get_world_bbox_points(elem, link_transform)
    check_crop(report, target_view, host_points, "Host / Linked Element Position")
    check_section_box(report, target_view, host_points, "Host / Linked Element Position")
    check_view_range(report, target_view, host_points, "Host / Linked Element Position")

    # Category using the host category table. Built-in model category IDs are normally
    # consistent across documents, so this is a useful By Host View check.
    try:
        if elem.Category:
            if target_view.GetCategoryHidden(elem.Category.Id):
                report.fail(
                    "Host / Linked Element Category",
                    "Category '{}' is hidden in the host target view.".format(elem.Category.Name),
                    "Enable this category in host Visibility/Graphics or review link display settings."
                )
            else:
                report.ok(
                    "Host / Linked Element Category",
                    "Category '{}' is not hidden by host category visibility.".format(elem.Category.Name)
                )
    except Exception:
        report.info(
            "Host / Linked Element Category",
            "Host category visibility could not be evaluated for this linked element."
        )

    linked_view = report_link_settings(report, target_view, link_inst, link_doc)
    check_linked_view_rules(report, linked_view, elem, link_doc)

    # Linked workset info even when no linked view is assigned.
    check_workset(report, target_view, elem, link_doc, "Linked Element")
    check_phase(report, target_view, elem, link_doc, "Linked Element")
    check_design_option(report, target_view, elem, "Linked Element")

    # Nested-link warning: if the selected linked element is itself a RevitLinkInstance,
    # this is the point where a second link level begins.
    if isinstance(elem, DB.RevitLinkInstance):
        report.warn(
            "Nested Revit Link",
            "The picked linked element is itself a RevitLinkInstance. This MVP diagnoses the first link level, but does not yet let you pick an element inside the nested link through the host UI.",
            "Open the parent linked model and run the tool there, or extend the tool with nested-link traversal."
        )

report.print()

output.print_md("")
output.print_md("> **MVP limitation:** Revit does not expose every Visibility/Graphics rule as one unified 'why hidden' API. "
                "This tool confirms rules that can be queried safely and marks uncertain areas as WARN/INFO instead of guessing.")
