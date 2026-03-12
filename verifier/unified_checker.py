"""
unified_checker.py — Single entry point for proof verification.

Routes all verification through System E as the primary engine, with
automatic T bridge fallback for completeness.

Usage:
    # Verify a System E proof
    result = verify_proof(eproof)

    # Verify a System E proof with T-bridge fallback
    result = verify_proof(eproof, use_t_fallback=True)

    # Verify proof from UI JSON
    result = verify_e_proof_json(proof_json)

    # Single-step verification
    ok = verify_step(known_literals, query_literal)

    # Get available rules for UI display
    rules = get_available_rules()
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from .e_ast import (
    Sort, Literal, Sequent, EProof, ETheorem,
    ProofStep, StepKind, EProofLine,
    substitute_literal, literal_vars,
)
from .e_checker import EChecker, ECheckResult
from .e_consequence import ConsequenceEngine
from .e_library import E_THEOREM_LIBRARY, get_theorems_up_to
from .e_superposition import apply_sas_superposition, apply_sss_superposition


# ═══════════════════════════════════════════════════════════════════════
# Unified result type
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class UnifiedResult:
    """Result of unified verification.

    Wraps an ECheckResult with additional metadata about which engine
    was used and whether T-bridge fallback was invoked.
    """
    valid: bool = False
    engine: str = "e"          # "e" or "t_fallback"
    e_result: Optional[ECheckResult] = None
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    diagnostics: List[str] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        """Alias for backward compatibility with old VerificationResult."""
        return self.valid

    def to_dict(self) -> dict:
        """Serialize for JSON/UI consumption."""
        return {
            "valid": self.valid,
            "accepted": self.valid,
            "engine": self.engine,
            "errors": self.errors,
            "warnings": self.warnings,
            "diagnostics": self.diagnostics,
        }


# ═══════════════════════════════════════════════════════════════════════
# Core verification: System E
# ═══════════════════════════════════════════════════════════════════════

def verify_proof(
    proof: EProof,
    theorems: Optional[Dict[str, ETheorem]] = None,
    use_t_fallback: bool = False,
) -> UnifiedResult:
    """Verify a System E proof.

    Primary path: System E checker.
    Fallback path (if use_t_fallback=True and E fails): translate to
    Tarski's system and check via the completeness pipeline.

    Args:
        proof: The System E proof to check.
        theorems: Theorem library for appeals. Defaults to the full
                  E_THEOREM_LIBRARY.
        use_t_fallback: If True, invoke T bridge when E is inconclusive.

    Returns:
        UnifiedResult with validity status and diagnostics.
    """
    if theorems is None:
        theorems = E_THEOREM_LIBRARY

    # ── Primary: System E ─────────────────────────────────────────
    checker = EChecker(theorems)
    e_result = checker.check_proof(proof)

    result = UnifiedResult(
        valid=e_result.valid,
        engine="e",
        e_result=e_result,
        errors=list(e_result.errors),
        warnings=list(e_result.warnings),
    )

    if e_result.valid:
        return result

    # ── Fallback: T bridge completeness ───────────────────────────
    if use_t_fallback:
        result = _try_t_fallback(proof, result)

    return result


def _try_t_fallback(proof: EProof, result: UnifiedResult) -> UnifiedResult:
    """Attempt T-bridge fallback for an E proof that failed direct check."""
    try:
        from .t_completeness import is_valid_for_ruler_compass

        seq = Sequent(
            hypotheses=list(proof.hypotheses),
            exists_vars=list(proof.exists_vars),
            conclusions=list(proof.goal),
        )

        comp_result = is_valid_for_ruler_compass(seq)
        result.diagnostics.extend(comp_result.diagnostics)

        if comp_result.is_valid:
            result.valid = True
            result.engine = "t_fallback"
            result.warnings.append(
                "Proof accepted via T-bridge completeness fallback."
            )
        else:
            result.diagnostics.append(
                "T-bridge fallback also failed to validate the sequent."
            )
    except Exception as exc:
        result.diagnostics.append(
            f"T-bridge fallback error: {exc}"
        )

    return result


# ═══════════════════════════════════════════════════════════════════════
# Named proof verification
# ═══════════════════════════════════════════════════════════════════════

def verify_named_proof(
    proof_name: str,
    use_t_fallback: bool = False,
) -> UnifiedResult:
    """Verify a named proof from the System E proof catalogue.

    Loads the proof from e_proofs and uses the theorem library
    (excluding the proposition being proved to prevent circularity).

    Args:
        proof_name: e.g. "Prop.I.1"
        use_t_fallback: If True, invoke T bridge when E is inconclusive.

    Returns:
        UnifiedResult with validity status.
    """
    from .e_proofs import get_proof

    proof = get_proof(proof_name)
    available = get_theorems_up_to(proof_name)
    return verify_proof(proof, theorems=available,
                        use_t_fallback=use_t_fallback)


# ═══════════════════════════════════════════════════════════════════════
# JSON proof verification (used by the proof panel UI)
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class LineCheckResult:
    """Per-line verification result for UI display."""
    line_id: int
    valid: bool = True
    errors: List[str] = field(default_factory=list)


@dataclass
class PanelCheckResult:
    """Result of verify_e_proof_json, geared toward the proof panel UI."""
    accepted: bool = False
    line_results: Dict[int, LineCheckResult] = field(default_factory=dict)
    derived: Set[int] = field(default_factory=set)
    errors: List[str] = field(default_factory=list)
    diagnostics: List[Any] = field(default_factory=list)
    t_bridge_accepted: bool = False


def verify_e_proof_json(proof_json: dict) -> PanelCheckResult:
    """Parse and verify a proof in the panel's JSON format using System E.

    The JSON format mirrors what the proof panel's ``_build_proof_json``
    produces::

        {
          "name": "...",
          "declarations": {"points": [...], "lines": [...]},
          "premises": ["¬(a = b)", ...],
          "goal": "ab = ac, ab = bc",
          "lines": [
            {"id": 1, "depth": 0, "statement": "¬(a = b)",
             "justification": "Given", "refs": []},
            {"id": 2, "depth": 0,
             "statement": "center(a, α), on(b, α)",
             "justification": "let-circle", "refs": [1]},
            ...
          ]
        }

    All formulas are in System E syntax (``e_parser``).

    Returns:
        A ``PanelCheckResult`` with per-line pass/fail, derived set,
        and overall acceptance.
    """
    from .e_parser import parse_literal_list, EParseError
    from .e_construction import CONSTRUCTION_RULE_BY_NAME

    result = PanelCheckResult()

    # ── 1. Gather declarations → sort context ─────────────────────
    decl = proof_json.get("declarations", {})
    sort_ctx: Dict[str, Sort] = {}
    for p in decl.get("points", []):
        sort_ctx[p] = Sort.POINT
        sort_ctx[p.lower()] = Sort.POINT
        sort_ctx[p.upper()] = Sort.POINT
    for ln in decl.get("lines", []):
        sort_ctx[ln] = Sort.LINE
        sort_ctx[ln.lower()] = Sort.LINE
        sort_ctx[ln.upper()] = Sort.LINE

    # ── 2. Parse premises into literals ───────────────────────────
    premise_lits: List[Literal] = []
    for prem_str in proof_json.get("premises", []):
        try:
            lits = parse_literal_list(prem_str, sort_ctx)
            premise_lits.extend(lits)
        except EParseError as exc:
            result.errors.append(f"Premise parse error: {exc}")

    # ── 3. Parse goal ─────────────────────────────────────────────
    goal_lits: List[Literal] = []
    goal_parse_ok = True
    goal_str = proof_json.get("goal", "")
    if goal_str:
        try:
            goal_lits = parse_literal_list(goal_str, sort_ctx)
            if not goal_lits:
                goal_parse_ok = False
        except EParseError:
            goal_parse_ok = False

    # ── 4. Build checker state ────────────────────────────────────
    checker = EChecker(E_THEOREM_LIBRARY)

    # When verifying a proof *of* Prop.I.N, the prover may only cite
    # earlier propositions (I.1 … I.(N-1)), not the theorem being proved.
    proof_name = proof_json.get("name", "")
    if proof_name and proof_name in E_THEOREM_LIBRARY:
        available_theorems = get_theorems_up_to(proof_name)
    else:
        # For non-proposition proofs or unnamed proofs, all theorems
        # are available (user-level proof checking in the UI).
        available_theorems = E_THEOREM_LIBRARY

    # Register declared variables — only the original names, not the
    # lowercase/uppercase parsing helpers, to avoid combinatorial
    # explosion in axiom grounding.
    declared_names: Set[str] = set()
    for p in decl.get("points", []):
        declared_names.add(p)
    for ln in decl.get("lines", []):
        declared_names.add(ln)
    for name in declared_names:
        if name in sort_ctx and name not in checker.variables:
            checker.variables[name] = sort_ctx[name]
    # Load premises as known facts and register their variables.
    # Infer variable sorts from premises so that the consequence and
    # transfer engines see correct point/line/circle classification.
    _premise_vars: Dict[str, Sort] = {}
    for lit in premise_lits:
        checker.known.add(lit)
        checker.consequence_engine._collect_atom_var_sorts(
            lit.atom, _premise_vars)
    for vname, vsort in _premise_vars.items():
        if vname not in checker.variables:
            checker.variables[vname] = vsort

    # ── 4b. T and H consequence engines for cross-system steps ────
    from .t_consequence import TConsequenceEngine
    from .h_consequence import HConsequenceEngine
    from .t_bridge import e_literal_to_t, t_literal_to_e
    from .h_bridge import e_literal_to_h, h_literal_to_e
    from .t_ast import TLiteral as _TLit, TSort
    from .h_ast import HLiteral as _HLit, HSort

    t_engine = TConsequenceEngine()
    h_engine = HConsequenceEngine()

    def _e_known_to_t(known: Set[Literal]) -> Set[_TLit]:
        """Translate current E known-set to T literals."""
        t_known: Set[_TLit] = set()
        for lit in known:
            result = e_literal_to_t(lit)
            if result:
                for tl in result:
                    t_known.add(tl)
        return t_known

    def _e_known_to_h(known: Set[Literal]) -> Set[_HLit]:
        """Translate current E known-set to H literals."""
        h_known: Set[_HLit] = set()
        for lit in known:
            hl = e_literal_to_h(lit, sort_ctx)
            if hl is not None:
                h_known.add(hl)
        return h_known

    # ── 5. Check each proof line ──────────────────────────────────
    lines = proof_json.get("lines", [])
    premise_ids: Set[int] = set()

    # Track literals derived per line so that ref-restricted checking
    # can build a known-set from only the cited lines.
    line_lits: Dict[int, Set[Literal]] = {}

    # Track depth per line id for subproof scoping.
    line_depth: Dict[int, int] = {}

    def _ref_known(refs: List[int]) -> Set[Literal]:
        """Collect literals from referenced lines only."""
        rk: Set[Literal] = set()
        for r in refs:
            if r in premise_ids:
                rk.update(line_lits.get(r, set()))
            elif r in line_lits:
                rk.update(line_lits[r])
        return rk

    for line in lines:
        lid = line.get("id", 0)
        just = line.get("justification", "")
        stmt_str = line.get("statement", "")
        refs = line.get("refs", [])
        depth = line.get("depth", 0)
        lr = LineCheckResult(line_id=lid)

        # Record depth for subproof scoping
        line_depth[lid] = depth

        # Given lines → check against premises
        if just == "Given":
            premise_ids.add(lid)
            given_lits: Set[Literal] = set()
            try:
                lits = parse_literal_list(stmt_str, sort_ctx)
                for lit in lits:
                    if lit in premise_lits or lit in checker.known:
                        checker.known.add(lit)
                        given_lits.add(lit)
                    else:
                        lr.valid = False
                        lr.errors.append(
                            f"'{stmt_str}' is not among the declared premises.")
            except EParseError as exc:
                lr.valid = False
                lr.errors.append(f"Parse error: {exc}")
            line_lits[lid] = given_lits
            if lr.valid:
                result.derived.add(lid)
            result.line_results[lid] = lr
            continue

        # Parse the statement into literals
        try:
            step_lits = parse_literal_list(stmt_str, sort_ctx)
        except EParseError as exc:
            lr.valid = False
            lr.errors.append(f"Parse error: {exc}")
            result.line_results[lid] = lr
            continue

        if not step_lits:
            lr.valid = False
            lr.errors.append("Empty statement.")
            result.line_results[lid] = lr
            continue

        # Determine step kind from justification
        step_kind = _classify_justification(just)

        # Detect which formal system the statement uses
        step_system = _detect_system(stmt_str)

        if step_kind == StepKind.CONSTRUCTION:
            # Construction rule: match conclusion pattern to derive
            # var_map, then validate prerequisites against known facts.
            rule = CONSTRUCTION_RULE_BY_NAME.get(just)
            if rule is None:
                lr.valid = False
                lr.errors.append(f"Unknown construction rule '{just}'.")
            else:
                # Check prerequisites via pattern matching
                if rule.prereq_pattern:
                    _vm, prereq_err = _match_construction_prereqs(
                        rule, step_lits, checker.known, checker)
                    if prereq_err is not None:
                        lr.valid = False
                        lr.errors.append(prereq_err)
                if lr.valid:
                    for lit in step_lits:
                        checker.known.add(lit)
                        _infer_sorts_from_atom(lit.atom, sort_ctx)
                        for vname in _literal_var_names(lit):
                            if vname not in checker.variables:
                                checker.variables[vname] = _infer_sort(
                                    vname, sort_ctx)
        elif step_kind in (StepKind.DIAGRAMMATIC, StepKind.AXIOM_ELIM):
            # Named axiom rules (e.g. "Intersection 9", "Generality 3")
            # must cite the specific lines providing their prerequisites.
            # When a named axiom step has explicit refs, restrict the
            # known-fact pool to only the literals from those lines.
            # Generic "Diagrammatic" steps (no named rule) still use
            # the full known set.
            _is_named_axiom = (
                just not in ("Diagrammatic", "diagrammatic",
                             "Given", "Reit")
                and refs
            )
            if _is_named_axiom:
                eff_known = _ref_known(refs)
            else:
                eff_known = checker.known

            for lit in step_lits:
                if lit in checker.known:
                    continue
                # Try E engine first
                ok = checker.consequence_engine.is_consequence(
                    eff_known, lit)
                # If E doesn't accept and the step uses T syntax,
                # bridge known facts to T and check via T engine
                if not ok and step_system == "T":
                    t_known = _e_known_to_t(eff_known)
                    t_lits = e_literal_to_t(lit)
                    if t_lits:
                        ok = all(
                            tl in t_known or t_engine.is_consequence(
                                t_known, tl)
                            for tl in t_lits
                        )
                # If E doesn't accept and the step uses H syntax,
                # bridge known facts to H and check via H engine
                if not ok and step_system == "H":
                    h_known = _e_known_to_h(eff_known)
                    h_lit = e_literal_to_h(lit, sort_ctx)
                    if h_lit is not None:
                        ok = (h_lit in h_known or
                              h_engine.is_consequence(h_known, h_lit))
                # As a last resort for any system, try the other engines
                if not ok and step_system == "E":
                    # Try T fallback
                    t_known = _e_known_to_t(eff_known)
                    t_lits = e_literal_to_t(lit)
                    if t_lits and all(
                        tl in t_known or t_engine.is_consequence(
                            t_known, tl)
                        for tl in t_lits
                    ):
                        ok = True
                    # Try H fallback
                    if not ok:
                        h_known = _e_known_to_h(eff_known)
                        h_lit = e_literal_to_h(lit, sort_ctx)
                        if h_lit is not None and (
                            h_lit in h_known or
                            h_engine.is_consequence(h_known, h_lit)
                        ):
                            ok = True
                if ok:
                    checker.known.add(lit)
                else:
                    lr.valid = False
                    lr.errors.append(
                        f"Diagrammatic assertion {lit} is not a "
                        f"direct consequence of referenced facts."
                        if refs else
                        f"Diagrammatic assertion {lit} is not a "
                        f"direct consequence of known facts.")
        elif step_kind == StepKind.METRIC:
            for lit in step_lits:
                if lit in checker.known:
                    continue
                if checker.metric_engine.is_consequence(
                        checker.known, lit):
                    checker.known.add(lit)
                else:
                    lr.valid = False
                    lr.errors.append(
                        f"Metric assertion {lit} is not a "
                        f"consequence of known facts.")
        elif step_kind == StepKind.TRANSFER:
            # Compute diagrammatic closure first so that derived
            # negative facts (e.g. ¬between(g,h,d)) are available
            # for the transfer axiom grounding.
            closure = checker.consequence_engine.direct_consequences(
                checker.known, checker.variables)
            checker.known.update(closure)
            diagram_known = {l for l in checker.known if l.is_diagrammatic}
            metric_known = {l for l in checker.known if l.is_metric}
            # Pass checker.variables so that grounding uses properly
            # sorted variables (points vs lines) without extracting
            # from the closure (which can misclassify line names).
            derived = checker.transfer_engine.apply_transfers(
                diagram_known, metric_known, checker.variables)
            for lit in step_lits:
                if lit in checker.known or lit in derived:
                    checker.known.add(lit)
                else:
                    lr.valid = False
                    lr.errors.append(
                        f"Transfer assertion {lit} is not derivable.")
        elif step_kind in (StepKind.SUPERPOSITION_SAS, StepKind.SUPERPOSITION):
            # SAS superposition (§3.7): extract 6 point names from the
            # step literals and delegate to apply_sas_superposition.
            pts = _extract_superposition_points(step_lits)
            if pts is None or len(pts) < 6:
                lr.valid = False
                lr.errors.append(
                    "SAS requires conclusions mentioning exactly "
                    "6 distinct point variables (a,b,c,d,e,f).")
            else:
                a, b, c, d, e, f = pts[:6]
                sas_r = apply_sas_superposition(
                    checker.known, a, b, c, d, e, f)
                if not sas_r.valid:
                    lr.valid = False
                    lr.errors.append(f"SAS failed: {sas_r.error}")
                else:
                    for lit in sas_r.derived:
                        checker.known.add(lit)
                    for lit in step_lits:
                        checker.known.add(lit)
        elif step_kind == StepKind.SUPERPOSITION_SSS:
            # SSS superposition (§3.7): same pattern as SAS.
            pts = _extract_superposition_points(step_lits)
            if pts is None or len(pts) < 6:
                lr.valid = False
                lr.errors.append(
                    "SSS requires conclusions mentioning exactly "
                    "6 distinct point variables (a,b,c,d,e,f).")
            else:
                a, b, c, d, e, f = pts[:6]
                sss_r = apply_sss_superposition(
                    checker.known, a, b, c, d, e, f)
                if not sss_r.valid:
                    lr.valid = False
                    lr.errors.append(f"SSS failed: {sss_r.error}")
                else:
                    for lit in sss_r.derived:
                        checker.known.add(lit)
                    for lit in step_lits:
                        checker.known.add(lit)
        elif step_kind == StepKind.THEOREM_APP:
            # Theorem application (§3.2): look up the theorem, check that
            # every hypothesis is a consequence of known facts, then add
            # the conclusions.
            #
            # Supports both built-in propositions ("Prop.I.x") and
            # user-loaded lemmas ("Lemma:name").
            thm = None
            if just.startswith("Lemma:"):
                lemma_name = just[len("Lemma:"):]
                # Look up lemma in the proof JSON's lemma definitions
                for lem_def in proof_json.get("lemmas", []):
                    if lem_def.get("name") == lemma_name:
                        # Parse lemma premises and goal into literals
                        lem_hyps: List[Literal] = []
                        for p in lem_def.get("premises", []):
                            try:
                                lem_hyps.extend(
                                    parse_literal_list(p, sort_ctx))
                            except EParseError:
                                pass
                        lem_concls: List[Literal] = []
                        goal_s = lem_def.get("goal", "")
                        if goal_s:
                            try:
                                lem_concls = parse_literal_list(
                                    goal_s, sort_ctx)
                            except EParseError:
                                pass
                        # Build an ad-hoc ETheorem
                        from .e_ast import Sequent, ETheorem
                        thm = ETheorem(
                            name=lemma_name,
                            statement=lemma_name,
                            sequent=Sequent(
                                hypotheses=lem_hyps,
                                conclusions=lem_concls))
                        break
                if thm is None:
                    lr.valid = False
                    lr.errors.append(
                        f"Unknown lemma '{lemma_name}'. "
                        f"Load the lemma before citing it.")
            else:
                thm = available_theorems.get(just)
                if thm is None:
                    if just in E_THEOREM_LIBRARY:
                        lr.valid = False
                        lr.errors.append(
                            f"Cannot cite '{just}' when proving "
                            f"'{proof_name}' — only earlier "
                            f"propositions are allowed.")
                    else:
                        lr.valid = False
                        lr.errors.append(
                            f"Unknown theorem '{just}'.")
            if thm is not None:
                # Derive variable mapping from step literals vs
                # theorem conclusions so hypotheses can be checked
                # with the user's actual variable names.
                var_map = _match_theorem_var_map(
                    thm, step_lits, known=checker.known)
                # Check hypotheses of the theorem are met
                for hyp in thm.sequent.hypotheses:
                    inst = substitute_literal(hyp, var_map)
                    if inst not in checker.known:
                        # Try via consequence engines
                        if inst.is_diagrammatic:
                            ok = checker.consequence_engine.is_consequence(
                                checker.known, inst)
                        elif inst.is_metric:
                            ok = checker.metric_engine.is_consequence(
                                checker.known, inst)
                        else:
                            ok = inst in checker.known
                        if not ok:
                            lr.valid = False
                            lr.errors.append(
                                f"Theorem '{just}' hypothesis not "
                                f"met: {inst}")
                if lr.valid:
                    # Add substituted theorem conclusions to known
                    thm_derived: Set[Literal] = set()
                    for conc in thm.sequent.conclusions:
                        inst_conc = substitute_literal(conc, var_map)
                        checker.known.add(inst_conc)
                        thm_derived.add(inst_conc)
                    # Validate that each step literal is a consequence
                    # of the theorem's conclusions (not arbitrary).
                    for lit in step_lits:
                        if lit in thm_derived or lit in checker.known:
                            thm_derived.add(lit)
                        elif lit.is_metric:
                            if checker.metric_engine.is_consequence(
                                    checker.known, lit):
                                checker.known.add(lit)
                                thm_derived.add(lit)
                            else:
                                lr.valid = False
                                lr.errors.append(
                                    f"Step literal {lit} is not a "
                                    f"conclusion of '{just}'.")
                        elif lit.is_diagrammatic:
                            if checker.consequence_engine.is_consequence(
                                    checker.known, lit):
                                checker.known.add(lit)
                                thm_derived.add(lit)
                            else:
                                lr.valid = False
                                lr.errors.append(
                                    f"Step literal {lit} is not a "
                                    f"conclusion of '{just}'.")
                        else:
                            lr.valid = False
                            lr.errors.append(
                                f"Step literal {lit} is not a "
                                f"conclusion of '{just}'.")
                    # Record all theorem-derived literals for this line
                    line_lits[lid] = thm_derived
        elif step_kind == StepKind.INDIRECT:
            # Indirect proof (reductio ad absurdum).
            # Format: "Indirect[Prop.I.3,Prop.I.4]"
            # The cited propositions must all be available (earlier than
            # the proof being checked).  The step's assertions are
            # accepted as conclusions of the indirect argument.
            import re as _re
            cited_match = _re.search(r'\[([^\]]+)\]', just)
            cited_names = []
            if cited_match:
                cited_names = [s.strip()
                               for s in cited_match.group(1).split(",")]
            if not cited_names:
                lr.valid = False
                lr.errors.append(
                    "Indirect proof must cite at least one earlier "
                    "proposition, e.g. Indirect[Prop.I.3,Prop.I.4].")
            else:
                for cn in cited_names:
                    if cn not in available_theorems:
                        if cn in E_THEOREM_LIBRARY:
                            lr.valid = False
                            lr.errors.append(
                                f"Cannot cite '{cn}' — only earlier "
                                f"propositions are allowed.")
                        else:
                            lr.valid = False
                            lr.errors.append(
                                f"Unknown proposition '{cn}'.")
            if lr.valid:
                for lit in step_lits:
                    checker.known.add(lit)
        elif step_kind == StepKind.CONTRADICTION:
            # Fitch ⊥-intro: derive ⊥ from contradictory refs.
            #
            # Protocol:
            #   The refs must include lines whose literals contain
            #   ψ and ¬ψ for some ψ, or a metric contradiction
            #   (X = Y and X < Y).  The step asserts ⊥.
            #
            from .e_ast import BOTTOM, Equals as _Eq, LessThan as _Lt
            ref_lits: Set[Literal] = set()
            for r in refs:
                ref_lits.update(line_lits.get(r, set()))
            found_contra = False
            for rl in ref_lits:
                if rl.negated() in ref_lits:
                    found_contra = True
                    break
            if not found_contra:
                # Check metric contradictions in ref lits
                m_lits = [l for l in ref_lits if l.is_metric]
                for m1 in m_lits:
                    for m2 in m_lits:
                        if m1 == m2:
                            continue
                        if (m1.polarity and m2.polarity
                                and isinstance(m1.atom, _Eq)
                                and isinstance(m2.atom, _Lt)):
                            if ((m1.atom.left == m2.atom.left
                                    and m1.atom.right == m2.atom.right)
                                or (m1.atom.left == m2.atom.right
                                    and m1.atom.right == m2.atom.left)):
                                found_contra = True
                                break
                    if found_contra:
                        break
            if not found_contra:
                lr.valid = False
                lr.errors.append(
                    "⊥-intro requires refs containing ψ and ¬ψ "
                    "(or X = Y and X < Y) but none found.")
            if lr.valid:
                checker.known.add(BOTTOM)
                # Record BOTTOM as this line's literal so Reductio
                # / ⊥-elim can reference it.
                line_lits[lid] = {BOTTOM}
        elif step_kind == StepKind.REDUCTIO:
            # Structured reductio ad absurdum.
            #
            # Protocol:
            #   1. An earlier "Assume" line introduced ¬φ (or φ).
            #   2. Subsequent steps derived facts from the assumption.
            #   3. This "Reductio" step asserts φ (the negation of the
            #      assumed literal), provided the current known set
            #      contains a contradiction: ψ and ¬ψ for some ψ.
            #
            # refs[0] must point to the Assume line.
            #
            if not refs:
                lr.valid = False
                lr.errors.append(
                    "Reductio must reference the Assume line as "
                    "refs[0].")
            else:
                assume_lid = refs[0]
                assume_lits = line_lits.get(assume_lid, set())
                if not assume_lits:
                    lr.valid = False
                    lr.errors.append(
                        f"Reductio refs[0] (line {assume_lid}) has "
                        f"no recorded literals.")
                else:
                    # The assumed literal(s) — usually a single ¬φ
                    assumed = list(assume_lits)
                    # Verify that each step literal is the negation of
                    # an assumed literal.
                    for lit in step_lits:
                        neg_lit = lit.negated()
                        if neg_lit not in assume_lits:
                            lr.valid = False
                            lr.errors.append(
                                f"Reductio conclusion {lit} is not "
                                f"the negation of any assumed "
                                f"literal.")

                    # Check for contradiction in known facts.
                    # Accept either:
                    #   (a) Fitch ⊥-elim: BOTTOM is in known (from a
                    #       prior ⊥-intro / Contradiction step), or
                    #   (b) Classic Reductio: ψ and ¬ψ both in known,
                    #       or a metric contradiction (X = Y and X < Y).
                    if lr.valid:
                        from .e_ast import BOTTOM as _BOTTOM
                        found_contradiction = _BOTTOM in checker.known
                        if not found_contradiction:
                            for kf in checker.known:
                                neg_kf = kf.negated()
                                if neg_kf in checker.known:
                                    found_contradiction = True
                                    break
                        if not found_contradiction:
                            # Also check metric contradictions:
                            # e.g. ψ < φ and φ < ψ, or ψ = φ and ψ < φ
                            from .e_ast import Equals, LessThan
                            metric_lits = [
                                l for l in checker.known if l.is_metric]
                            for m1 in metric_lits:
                                for m2 in metric_lits:
                                    if m1 == m2:
                                        continue
                                    # area(X) = area(Y) and area(X) < area(Y)
                                    if (m1.polarity and m2.polarity
                                            and isinstance(m1.atom, Equals)
                                            and isinstance(m2.atom, LessThan)):
                                        if (m1.atom.left == m2.atom.left
                                                and m1.atom.right == m2.atom.right):
                                            found_contradiction = True
                                            break
                                        if (m1.atom.left == m2.atom.right
                                                and m1.atom.right == m2.atom.left):
                                            found_contradiction = True
                                            break
                                    if found_contradiction:
                                        break

                        if not found_contradiction:
                            lr.valid = False
                            lr.errors.append(
                                "Reductio requires a contradiction "
                                "in the known facts (ψ and ¬ψ for "
                                "some ψ), but none was found.")

                    # If valid, retract all facts derived inside the
                    # subproof (at the Assume's depth or deeper) and
                    # add only the Reductio conclusion at the outer
                    # depth.  This prevents subproof-scoped facts from
                    # leaking into the enclosing proof.
                    if lr.valid:
                        assume_depth = line_depth.get(assume_lid, 0)
                        # Collect all line ids at subproof depth between
                        # the Assume line and this Reductio line.
                        subproof_lits: Set[Literal] = set()
                        for prev_line in lines:
                            plid = prev_line.get("id", 0)
                            if plid == lid:
                                break  # stop at the current Reductio line
                            pdepth = line_depth.get(plid, 0)
                            if plid >= assume_lid and pdepth >= assume_depth:
                                subproof_lits.update(
                                    line_lits.get(plid, set()))
                        # Retract subproof-scoped facts
                        for sl in subproof_lits:
                            checker.known.discard(sl)
                        # Add Reductio conclusion
                        for lit in step_lits:
                            checker.known.add(lit)
        elif just == "Assume":
            # Assumptions in subproofs
            for lit in step_lits:
                checker.known.add(lit)
        else:
            # Unknown justification — reject the step
            lr.valid = False
            lr.errors.append(
                f"Unknown justification '{just}'. Use a recognized "
                f"rule name (e.g. let-line, let-circle, Diagrammatic, "
                f"Metric, Transfer, SAS, Prop.I.x, "
                f"Indirect[Prop.I.x,...], Assume, Reductio).")

        if lr.valid:
            result.derived.add(lid)
            # Record per-line literals for ref-restricted checking.
            # Theorem application already sets line_lits[lid] with
            # the full conclusion set; other step kinds use step_lits.
            if lid not in line_lits:
                line_lits[lid] = set(step_lits)
        result.line_results[lid] = lr

    # ── 6. Check goal ─────────────────────────────────────────────
    if goal_str and not goal_parse_ok:
        # Goal specified but could not be parsed — never accept
        goal_met = False
        result.errors.append(
            "Goal formula could not be parsed. "
            "Check syntax (parenthesized MagAdd, △, ∠, etc.).")
    elif goal_str and not goal_lits:
        # Goal string present but parsed to empty — never accept
        goal_met = False
    else:
        goal_met = all(lit in checker.known for lit in goal_lits)
    result.accepted = goal_met and all(
        lr.valid for lr in result.line_results.values())

    if not goal_met and goal_lits:
        missing = [lit for lit in goal_lits if lit not in checker.known]
        result.errors.append(
            f"Goal not established. Missing: "
            f"{', '.join(repr(m) for m in missing)}")

    return result


def _extract_superposition_points(
    step_lits: List[Literal],
) -> Optional[List[str]]:
    """Extract the 6 triangle point names from SAS/SSS conclusion literals.

    SAS conclusions look like:  bc = ef, ∠abc = ∠def, ∠acb = ∠dfe
    SSS conclusions look like:  ∠bac = ∠edf, ∠abc = ∠def, ∠acb = ∠dfe

    We extract (a,b,c,d,e,f) by finding the first angle equality and
    reading its three point names on each side.

    Returns a list of 6 point name strings [a,b,c,d,e,f] where:
      - a,b,c are the first triangle
      - d,e,f are the second triangle (same vertex correspondence)
    Or None if extraction fails.
    """
    from .e_ast import Equals, AngleTerm

    # Find the first angle equality to get the 3+3 triangle points
    for lit in step_lits:
        if not lit.polarity:
            continue
        atom = lit.atom
        if not isinstance(atom, Equals):
            continue
        lhs, rhs = atom.left, atom.right
        if isinstance(lhs, AngleTerm) and isinstance(rhs, AngleTerm):
            # ∠p1p2p3 = ∠q1q2q3
            # Triangle 1 = (p1, p2, p3), Triangle 2 = (q1, q2, q3)
            # The vertex correspondence is p1↔q1, p2↔q2, p3↔q3
            tri1 = [lhs.p1, lhs.p2, lhs.p3]
            tri2 = [rhs.p1, rhs.p2, rhs.p3]
            return tri1 + tri2

    return None


def _classify_justification(just: str) -> Optional[StepKind]:
    """Map a justification string to a StepKind."""
    from .e_construction import CONSTRUCTION_RULE_BY_NAME

    if just in CONSTRUCTION_RULE_BY_NAME:
        return StepKind.CONSTRUCTION

    # Proposition references (Prop.I.1, etc.)
    if just.startswith("Prop.") or just.startswith("prop."):
        return StepKind.THEOREM_APP

    # Lemma references (Lemma:name)
    if just.startswith("Lemma:"):
        return StepKind.THEOREM_APP

    # Explicit step kind labels
    _MAP = {
        "diagrammatic": StepKind.DIAGRAMMATIC,
        "Diagrammatic": StepKind.DIAGRAMMATIC,
        "metric": StepKind.METRIC,
        "Metric": StepKind.METRIC,
        "transfer": StepKind.TRANSFER,
        "Transfer": StepKind.TRANSFER,
        "SAS": StepKind.SUPERPOSITION_SAS,
        "SSS": StepKind.SUPERPOSITION_SSS,
        "SAS Superposition": StepKind.SUPERPOSITION_SAS,
        "SSS Superposition": StepKind.SUPERPOSITION_SSS,
        "SAS-elim": StepKind.SUPERPOSITION_SAS,
        "SSS-elim": StepKind.SUPERPOSITION_SSS,
        "Reit": StepKind.DIAGRAMMATIC,
        "Given": StepKind.DIAGRAMMATIC,
    }
    kind = _MAP.get(just)
    if kind is not None:
        return kind

    # Named axiom rules from the rule catalogue (§3.4–§3.7).
    # Match by category-based prefixes so every rule shown in the
    # dropdown is accepted as a valid justification.
    _DIAG_PREFIXES = (
        "Generality", "Betweenness", "Same-side", "Pasch",
        "Triple incidence", "Circle", "Intersection",
    )
    for pfx in _DIAG_PREFIXES:
        if just.startswith(pfx):
            return StepKind.DIAGRAMMATIC

    _METRIC_PREFIXES = ("CN", "M1", "M2", "M3", "M4", "M5", "M6",
                        "M7", "M8", "M9", "< ", "+ ")
    for pfx in _METRIC_PREFIXES:
        if just.startswith(pfx):
            return StepKind.METRIC

    _TRANSFER_PREFIXES = ("Segment transfer", "Angle transfer",
                           "Area transfer")
    for pfx in _TRANSFER_PREFIXES:
        if just.startswith(pfx):
            return StepKind.TRANSFER

    # Indirect proof (reductio ad absurdum) citing earlier propositions
    if just.startswith("Indirect"):
        return StepKind.INDIRECT

    # Structured reductio: Assume ¬φ, derive ψ ∧ ¬ψ, conclude φ
    if just == "Reductio":
        return StepKind.REDUCTIO

    # Fitch ⊥-intro: derive ⊥ from ψ and ¬ψ
    if just in ("Contradiction", "⊥-intro"):
        return StepKind.CONTRADICTION

    # Fitch ⊥-elim: discharge Assume by citing ⊥ line
    if just in ("⊥-elim",):
        return StepKind.REDUCTIO

    # Default: unrecognised
    return None


# ═══════════════════════════════════════════════════════════════════════
# System detection — identifies E / T / H from statement syntax
# ═══════════════════════════════════════════════════════════════════════

_T_PREDICATES = {"B", "Cong", "Eq", "Neq", "NotB", "NotCong"}
_H_PREDICATES = {"IncidL", "BetH", "CongH", "CongaH", "ColH",
                 "EqPt", "EqL", "Para", "SameSideH"}


def _detect_system(statement: str) -> str:
    """Detect which formal system a statement uses.

    Returns ``"E"``, ``"T"``, or ``"H"`` by scanning for system-specific
    predicate names.  Falls back to ``"E"`` when no T/H predicate is found.
    """
    import re
    # Look for predicate names followed by '(' to avoid matching
    # variable names that happen to be the same letters.
    for pred in _H_PREDICATES:
        if re.search(rf'\b{pred}\s*\(', statement):
            return "H"
    for pred in _T_PREDICATES:
        if re.search(rf'\b{pred}\s*\(', statement):
            return "T"
    return "E"


def _literal_var_names(lit: Literal) -> Set[str]:
    """Extract variable names from a literal."""
    from .e_ast import atom_vars
    return atom_vars(lit.atom)


# ═══════════════════════════════════════════════════════════════════════
# Pattern matching — derive var_map from rule patterns vs step literals
# ═══════════════════════════════════════════════════════════════════════

def _atom_fields(atom) -> Optional[Tuple[type, Tuple[str, ...]]]:
    """Return (atom_class, (string_fields...)) for pattern matching.

    Handles both diagrammatic atoms (On, Between, etc.) whose fields
    are plain strings, and metric Equals atoms whose fields are Term
    sub-expressions containing point-name strings.
    """
    from .e_ast import (On, SameSide, Between, Center, Inside,
                        Intersects, Equals, LessThan,
                        SegmentTerm, AngleTerm, AreaTerm)
    if isinstance(atom, On):
        return (On, (atom.point, atom.obj))
    if isinstance(atom, SameSide):
        return (SameSide, (atom.a, atom.b, atom.line))
    if isinstance(atom, Between):
        return (Between, (atom.a, atom.b, atom.c))
    if isinstance(atom, Center):
        return (Center, (atom.point, atom.circle))
    if isinstance(atom, Inside):
        return (Inside, (atom.point, atom.circle))
    if isinstance(atom, Intersects):
        return (Intersects, (atom.obj1, atom.obj2))
    if isinstance(atom, Equals):
        if isinstance(atom.left, str) and isinstance(atom.right, str):
            return (Equals, (atom.left, atom.right))
        # Metric Equals: flatten Term sub-expressions into string tuples
        lf = _term_fields(atom.left)
        rf = _term_fields(atom.right)
        if lf is not None and rf is not None:
            tag = (Equals, type(atom.left).__name__,
                   type(atom.right).__name__)
            return (tag, lf + rf)
    if isinstance(atom, LessThan):
        lf = _term_fields(atom.left)
        rf = _term_fields(atom.right)
        if lf is not None and rf is not None:
            tag = (LessThan, type(atom.left).__name__,
                   type(atom.right).__name__)
            return (tag, lf + rf)
    return None


def _term_fields(t) -> Optional[Tuple[str, ...]]:
    """Extract point-name strings from a Term for pattern matching."""
    from .e_ast import SegmentTerm, AngleTerm, AreaTerm, MagAdd, RightAngle, ZeroMag
    if isinstance(t, str):
        return (t,)
    if isinstance(t, SegmentTerm):
        return (t.p1, t.p2)
    if isinstance(t, AngleTerm):
        return (t.p1, t.p2, t.p3)
    if isinstance(t, AreaTerm):
        return (t.p1, t.p2, t.p3)
    if isinstance(t, RightAngle):
        return ("__right_angle__",)
    if isinstance(t, ZeroMag):
        return ("__zero__",)
    if isinstance(t, MagAdd):
        left_f = _term_fields(t.left)
        right_f = _term_fields(t.right)
        if left_f is not None and right_f is not None:
            return left_f + right_f
    return None


def _try_match_literal(
    pattern: Literal, concrete: Literal, bindings: Dict[str, str]
) -> Optional[Dict[str, str]]:
    """Try to unify *pattern* with *concrete*, extending *bindings*.

    Returns updated bindings on success, ``None`` on failure.
    The original *bindings* dict is not mutated.
    Handles Equals symmetry: tries both orderings for Equals atoms.
    """
    if pattern.polarity != concrete.polarity:
        return None
    pf = _atom_fields(pattern.atom)
    cf = _atom_fields(concrete.atom)
    if pf is None or cf is None:
        return None
    pat_cls, pat_args = pf
    con_cls, con_args = cf
    if pat_cls != con_cls or len(pat_args) != len(con_args):
        return None

    # Try direct match
    result = _try_bind(pat_args, con_args, bindings)
    if result is not None:
        return result

    # For Equals-like atoms, try swapped match (symmetry)
    from .e_ast import Equals
    is_eq = (pat_cls is Equals or
             (isinstance(pat_cls, tuple) and pat_cls[0] is Equals))
    if is_eq and len(pat_args) >= 2:
        # Determine the split point: for Equals on Terms, each side
        # contributes half the fields.
        half = len(pat_args) // 2
        swapped_con = con_args[half:] + con_args[:half]
        result = _try_bind(pat_args, swapped_con, bindings)
        if result is not None:
            return result

    return None


def _try_bind(
    pat_args: Tuple[str, ...],
    con_args: Tuple[str, ...],
    bindings: Dict[str, str],
) -> Optional[Dict[str, str]]:
    """Try to unify pattern args with concrete args."""
    new_bindings = dict(bindings)
    for pvar, cval in zip(pat_args, con_args):
        if pvar in new_bindings:
            if new_bindings[pvar] != cval:
                return None
        else:
            new_bindings[pvar] = cval
    return new_bindings


def _match_construction_prereqs(
    rule,
    step_lits: List[Literal],
    known: Set[Literal],
    checker,
) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    """Derive a var_map from *step_lits* vs the rule's conclusion pattern,
    then check that every prerequisite (instantiated) is in *known* or
    derivable via the consequence engine.

    Returns ``(var_map, error_msg)``.  *error_msg* is ``None`` on success.
    """
    bindings: Dict[str, str] = {}
    remaining = list(step_lits)  # track unconsumed step literals

    for pat_lit in rule.conclusion_pattern:
        matched = False
        for i, step_lit in enumerate(remaining):
            result = _try_match_literal(pat_lit, step_lit, bindings)
            if result is not None:
                bindings = result
                remaining.pop(i)  # consume this step literal
                matched = True
                break
        if not matched:
            # Could not match this conclusion pattern element.
            # The step text does not match the rule's expected output,
            # so the construction is invalid.
            return None, (
                f"Statement does not match '{rule.name}' "
                f"conclusion pattern. Expected literals matching: "
                f"{', '.join(repr(p) for p in rule.conclusion_pattern)}")

    # All conclusion patterns matched — now check prerequisites.
    # Some prerequisites may contain schema variables not present in the
    # conclusion pattern (e.g. ``center(c, α)`` where ``c`` only appears
    # in the prereqs).  We attempt to bind these by searching *known*
    # for a matching literal.
    for prereq in rule.prereq_pattern:
        inst = substitute_literal(prereq, bindings)
        if inst in known:
            continue

        # Check if the instantiated prereq still contains unbound schema
        # variables (variables that were in the original prereq but not
        # yet in bindings).  If so, try to find a known literal that
        # matches and extends the bindings.
        prereq_vars = set(literal_vars(prereq))
        unbound = prereq_vars - set(bindings.keys())
        if unbound:
            resolved = False
            for klit in known:
                result = _try_match_literal(inst, klit, bindings)
                if result is not None:
                    bindings = result
                    resolved = True
                    break
            if resolved:
                continue

        # Re-instantiate with potentially updated bindings
        inst = substitute_literal(prereq, bindings)
        if inst in known:
            continue
        # Try consequence engine
        ok = checker.consequence_engine.is_consequence(
            known, inst)
        if not ok:
            return bindings, (
                f"Construction prerequisite not met: {inst}")
    return bindings, None


def _match_theorem_var_map(
    thm: ETheorem,
    step_lits: List[Literal],
    known: Optional[Set[Literal]] = None,
) -> Dict[str, str]:
    """Derive a variable mapping from step literals matched against
    the theorem's conclusions.  Falls back to an empty mapping if
    pattern matching fails (variables happen to be the same).

    When *known* is provided, hypothesis variables that don't appear in
    the conclusions are bound by matching hypotheses against known facts.
    This handles theorems like Prop.I.2 where the line variable ``L``
    appears only in hypotheses (``on(b, L)``) but not in the conclusion
    (``af = bc``).
    """
    bindings: Dict[str, str] = {}
    remaining = list(step_lits)  # track unconsumed step literals
    for conc in thm.sequent.conclusions:
        for i, step_lit in enumerate(remaining):
            result = _try_match_literal(conc, step_lit, bindings)
            if result is not None:
                bindings = result
                remaining.pop(i)  # consume this step literal
                break

    # If known facts are available, try to bind hypothesis-only variables
    # by matching each unresolved hypothesis against known facts.
    # First tries the identity mapping (same variable names) since many
    # proofs use the theorem's variable names directly.
    if known is not None:
        from .e_ast import atom_vars, literal_vars, Equals

        conc_vars: Set[str] = set()
        for conc in thm.sequent.conclusions:
            conc_vars |= literal_vars(conc)

        # Collect all hypothesis variables that need binding
        all_hyp_vars: Set[str] = set()
        for hyp in thm.sequent.hypotheses:
            all_hyp_vars |= literal_vars(hyp)
        unbound_vars = all_hyp_vars - set(bindings.keys())

        if unbound_vars:
            # Strategy 1: identity mapping — use the theorem's own names
            identity = dict(bindings)
            for v in unbound_vars:
                identity[v] = v
            all_met = True
            for hyp in thm.sequent.hypotheses:
                inst = substitute_literal(hyp, identity)
                if inst not in known:
                    all_met = False
                    break
            if all_met:
                bindings = identity
            else:
                # Strategy 2: greedy matching with backtracking
                hyps_needing_bind = []
                for hyp in thm.sequent.hypotheses:
                    hyp_vars = literal_vars(hyp)
                    if hyp_vars - set(bindings.keys()) - conc_vars:
                        hyps_needing_bind.append(hyp)

                def _validate(candidate: Dict[str, str]) -> bool:
                    for h in thm.sequent.hypotheses:
                        inst = substitute_literal(h, candidate)
                        if (not inst.polarity
                                and isinstance(inst.atom, Equals)
                                and isinstance(inst.atom.left, str)
                                and inst.atom.left == inst.atom.right):
                            return False
                        if inst not in known:
                            # Check if all variables are bound
                            inst_vars = literal_vars(inst)
                            fully_bound = all(
                                v in candidate.values()
                                for v in inst_vars)
                            if fully_bound:
                                return False
                    return True

                def _backtrack(
                    idx: int, current: Dict[str, str]
                ) -> Optional[Dict[str, str]]:
                    if idx >= len(hyps_needing_bind):
                        return current if _validate(current) else None
                    hyp = hyps_needing_bind[idx]
                    hyp_vars = literal_vars(hyp)
                    unbound = hyp_vars - set(current.keys()) - conc_vars
                    if not unbound:
                        return _backtrack(idx + 1, current)
                    for kf in known:
                        candidate = _try_match_literal(hyp, kf, current)
                        if candidate is not None:
                            result = _backtrack(idx + 1, candidate)
                            if result is not None:
                                return result
                    return _backtrack(idx + 1, current)

                if hyps_needing_bind:
                    result = _backtrack(0, dict(bindings))
                    if result is not None:
                        bindings = result

    return bindings


def _infer_sorts_from_atom(atom, sort_ctx: Dict[str, Sort]) -> None:
    """Update sort_ctx based on the structural roles of variables in an atom.

    For example, ``Center(point, circle)`` tells us the second argument
    must be a circle, and ``On(point, obj)`` tells us the first argument
    is a point.
    """
    from .e_ast import On, Center, Inside, Intersects, SameSide, Between

    if isinstance(atom, Center):
        sort_ctx.setdefault(atom.point, Sort.POINT)
        sort_ctx[atom.circle] = Sort.CIRCLE  # always override — definitive
    elif isinstance(atom, Inside):
        sort_ctx.setdefault(atom.point, Sort.POINT)
        sort_ctx[atom.circle] = Sort.CIRCLE
    elif isinstance(atom, On):
        sort_ctx.setdefault(atom.point, Sort.POINT)
        # obj could be line or circle — only set if not yet known
        sort_ctx.setdefault(atom.obj, _infer_sort(atom.obj, sort_ctx))
    elif isinstance(atom, Intersects):
        sort_ctx.setdefault(atom.obj1, _infer_sort(atom.obj1, sort_ctx))
        sort_ctx.setdefault(atom.obj2, _infer_sort(atom.obj2, sort_ctx))
    elif isinstance(atom, Between):
        for v in (atom.a, atom.b, atom.c):
            sort_ctx.setdefault(v, Sort.POINT)
    elif isinstance(atom, SameSide):
        sort_ctx.setdefault(atom.a, Sort.POINT)
        sort_ctx.setdefault(atom.b, Sort.POINT)
        sort_ctx.setdefault(atom.line, Sort.LINE)


def _infer_sort(name: str, sort_ctx: Dict[str, Sort]) -> Sort:
    """Infer the sort of a variable from context or naming convention."""
    if name in sort_ctx:
        return sort_ctx[name]
    # Greek letters (Unicode) → circle
    if any('\u03b1' <= ch <= '\u03c9' for ch in name):
        return Sort.CIRCLE
    # Latin-spelled Greek letter names → circle
    _GREEK_NAMES = {
        "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta",
        "theta", "iota", "kappa", "lambda", "mu", "nu", "xi",
        "omicron", "pi", "rho", "sigma", "tau", "upsilon", "phi",
        "chi", "psi", "omega",
    }
    if name.lower() in _GREEK_NAMES:
        return Sort.CIRCLE
    # Lowercase single letter → point (System E convention)
    if len(name) == 1 and name.islower():
        return Sort.POINT
    # Uppercase single letter → line
    if len(name) == 1 and name.isupper():
        return Sort.LINE
    return Sort.POINT


# ═══════════════════════════════════════════════════════════════════════
# Single-step verification
# ═══════════════════════════════════════════════════════════════════════

def verify_step(
    known: Set[Literal],
    query: Literal,
    use_smt_fallback: bool = False,
    z3_path: str = "z3",
    timeout_ms: int = 5000,
) -> bool:
    """Check whether a single literal follows from a set of known literals.

    Uses the System E consequence engine first. If ``use_smt_fallback``
    is True and forward-chaining is inconclusive, falls back to an SMT
    solver (Z3) to check the obligation.

    Args:
        known: Set of currently established literals.
        query: The literal to verify.
        use_smt_fallback: If True, try Z3 when forward-chaining fails.
        z3_path: Path to the Z3 binary.
        timeout_ms: SMT solver timeout in milliseconds.

    Returns:
        True if query is a consequence of known.
    """
    engine = ConsequenceEngine()
    if engine.is_consequence(known, query):
        return True

    if not use_smt_fallback:
        return False

    # SMT fallback (Phase 8.3)
    try:
        from .smt_backend import try_consequence_then_smt
        result, _ = try_consequence_then_smt(
            list(known), query, z3_path=z3_path, timeout_ms=timeout_ms,
        )
        return result
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════
# Rule catalogue for UI
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class RuleInfo:
    """Display-friendly description of a rule / axiom."""
    name: str
    category: str      # "construction", "diagrammatic", "metric", "transfer"
    description: str
    section: str = ""  # Paper section reference


def get_available_rules() -> List[RuleInfo]:
    """Return all System E axioms and construction rules formatted for UI.

    Groups (per paper sections):
      - Construction rules (Section 3.3)
      - Diagrammatic axioms (Section 3.4)
      - Metric axioms (Section 3.5)
      - Transfer axioms (Section 3.6)
      - Superposition (Section 3.7)
      - Propositions (Book I theorems)
    """
    rules: List[RuleInfo] = []

    # ── Construction rules (§3.3) ─────────────────────────────────
    _CONSTRUCTION_DESCS = {
        "let-point": "Introduce a fresh point",
        "let-point-on-line": "Introduce a point on a given line",
        "let-point-on-line-between": "Point on line, between two given points",
        "let-point-on-line-extend": "Point on line, extending beyond a given point",
        "let-point-same-side": "Point on the same side of a line as another",
        "let-point-opposite-side": "Point on the opposite side of a line",
        "let-point-on-circle": "Point on a given circle",
        "let-point-inside-circle": "Point inside a given circle",
        "let-point-outside-circle": "Point outside a given circle",
        "let-line": "Construct the line through two distinct points",
        "let-circle": "Construct the circle with given center through a point",
        "let-intersection-line-line": "Intersection of two lines",
        "let-intersection-circle-line-one": "First intersection of circle and line",
        "let-intersection-circle-line-two": "Second intersection of circle and line",
        "let-intersection-line-circle-between": "Line–circle intersection (between variant)",
        "let-intersection-line-circle-extend": "Line–circle intersection (extend variant)",
        "let-intersection-circle-circle-one": "First intersection of two circles",
        "let-intersection-circle-circle-two": "Second intersection of two circles",
        "let-intersection-circle-circle-same-side": "Circle–circle intersection (same side)",
        "let-intersection-circle-circle-opposite-side": "Circle–circle intersection (opposite side)",
    }
    from .e_construction import ALL_CONSTRUCTION_RULES
    for cr in ALL_CONSTRUCTION_RULES:
        prereqs = ", ".join(str(p) for p in cr.prereq_pattern) if cr.prereq_pattern else "—"
        concls = ", ".join(str(c) for c in cr.conclusion_pattern) if cr.conclusion_pattern else "—"
        desc = _CONSTRUCTION_DESCS.get(cr.name, cr.name)
        rules.append(RuleInfo(
            name=cr.name,
            category="construction",
            description=f"{desc}  [{prereqs} ⇒ {concls}]",
            section="§3.3",
        ))

    # ── Diagrammatic axioms (§3.4) ────────────────────────────────
    from .e_axioms import (
        GENERALITY_AXIOMS, BETWEEN_AXIOMS, SAME_SIDE_AXIOMS,
        PASCH_AXIOMS, TRIPLE_INCIDENCE_AXIOMS, CIRCLE_AXIOMS,
        INTERSECTION_AXIOMS,
    )
    _DIAG_GROUPS = [
        ("Generality", GENERALITY_AXIOMS,
         ["Two points on two lines → points equal or lines equal",
          "Center uniqueness: center(a,α) ∧ center(b,α) → a = b",
          "Center is inside: center(a,α) → inside(a,α)",
          "Inside excludes on: inside(a,α) → ¬on(a,α)"]),
        ("Betweenness", BETWEEN_AXIOMS,
         ["between(a,b,c) → between(c,b,a)  (symmetry)",
          "between(a,b,c) → a ≠ b",
          "between(a,b,c) → a ≠ c",
          "between(a,b,c) → ¬between(b,a,c)  (strict ordering)",
          "between(a,b,c) ∧ on(a,L) ∧ on(b,L) → on(c,L)",
          "between(a,b,c) ∧ on(a,L) ∧ on(c,L) → on(b,L)",
          "between(a,b,c) ∧ between(a,d,b) → between(a,d,c)",
          "between(a,b,c) ∧ between(b,c,d) → between(a,b,d)",
          "Three collinear points: one is between the other two",
          "between(a,b,c) ∧ between(a,b,d) → ¬between(b,c,d)"]),
        ("Same-side", SAME_SIDE_AXIOMS,
         ["same-side(a,a,L) ∨ on(a,L)  (reflexivity)",
          "same-side(a,b,L) → same-side(b,a,L)  (symmetry)",
          "same-side(a,b,L) → ¬on(a,L)",
          "same-side(a,b,L) ∧ same-side(a,c,L) → same-side(b,c,L)  (transitivity)",
          "Any two points off a line: same-side or one is on the line"]),
        ("Pasch", PASCH_AXIOMS,
         ["same-side(a,c,L) ∧ between(a,b,c) → same-side(a,b,L)",
          "between(a,b,c) ∧ on(a,L) → same-side(b,c,L) ∨ on(b,L)",
          "between(a,b,c) ∧ on(b,L) → ¬same-side(a,c,L)",
          "Pasch: line crossing one side of a triangle hits another side"]),
        ("Triple incidence", TRIPLE_INCIDENCE_AXIOMS,
         ["Three concurrent lines determine collinear or same-side",
          "Concurrent lines: transitivity of same-side across lines",
          "Five-line same-side transitivity"]),
        ("Circle", CIRCLE_AXIOMS,
         ["Chord intersects interior: on(b,α) ∧ on(c,α) ∧ inside(a,α) → between",
          "inside ∧ between → inside (segment inside circle, variant 1)",
          "inside ∧ between → inside (boundary to interior, variant 1)",
          "inside ∧ between → inside (segment inside circle, variant 2)",
          "on ∧ between → inside (boundary to interior, variant 2)",
          "inside ∧ between → inside (variant 3)",
          "on ∧ between → inside (variant 3)",
          "inside ∧ between → inside (variant 4)",
          "on ∧ between → inside (variant 4)",
          "Two intersecting circles: intersection points on opposite sides"]),
        ("Intersection", INTERSECTION_AXIOMS,
         ["Opposite sides → lines intersect",
          "on(a,α) ∧ on(b,α): opposite sides of L → L intersects α",
          "on(a,α) ∧ inside(b,α): opposite sides of L → L intersects α",
          "inside(a,α) ∧ on(b,α): opposite sides of L → L intersects α",
          "inside(a,α) ∧ inside(b,α): opposite sides of L → L intersects α",
          "inside(a,α) ∧ on(a,L) → L intersects α",
          "Circles: on/inside combinations → intersects(α,β) (variant 1)",
          "Circles: inside/inside → intersects(α,β) (variant 2)",
          "Circles: on/inside mixed → intersects(α,β) (variant 3)"]),
    ]
    for group_name, axioms, descs in _DIAG_GROUPS:
        for i, ax in enumerate(axioms):
            desc = descs[i] if i < len(descs) else f"{group_name} axiom {i+1}"
            rules.append(RuleInfo(
                name=f"{group_name} {i+1}",
                category="diagrammatic",
                description=desc,
                section="§3.4",
            ))

    # ── Metric axioms (§3.5) ──────────────────────────────────────
    _METRIC_RULES = [
        ("CN1 — Transitivity", "a = b ∧ b = c → a = c"),
        ("CN2 — Addition", "a = b ∧ c = d → a+c = b+d"),
        ("CN3 — Subtraction", "a+c = b+c → a = b"),
        ("CN4 — Reflexivity", "a = a"),
        ("CN5 — Whole > Part", "0 < b → a < a+b"),
        ("M1 — Zero segment", "ab = 0 ↔ a = b"),
        ("M2 — Non-negative", "ab ≥ 0"),
        ("M3 — Symmetry", "ab = ba"),
        ("M4 — Angle symmetry", "a≠b ∧ a≠c → ∠abc = ∠cba"),
        ("M5 — Angle bounds", "0 ≤ ∠abc ≤ 2·right"),
        ("M6 — Degenerate area", "△aab = 0"),
        ("M7 — Non-negative area", "△abc ≥ 0"),
        ("M8 — Area symmetry", "△abc = △cab = △acb"),
        ("M9 — Congruence → area", "Full congruence → equal areas"),
        ("< trichotomy", "Exactly one of: a < b, a = b, b < a"),
        ("< transitivity", "a < b ∧ b < c → a < c"),
        ("+ monotonicity", "a < b → a+c < b+c"),
    ]
    for name, desc in _METRIC_RULES:
        rules.append(RuleInfo(
            name=name,
            category="metric",
            description=desc,
            section="§3.5",
        ))

    # ── Transfer axioms (§3.6) ────────────────────────────────────
    from .e_axioms import (
        DIAGRAM_SEGMENT_TRANSFER, DIAGRAM_ANGLE_TRANSFER,
        DIAGRAM_AREA_TRANSFER,
    )
    _TRANSFER_GROUPS = [
        ("Segment transfer", DIAGRAM_SEGMENT_TRANSFER,
         ["between(a,b,c) → ab + bc = ac  (segment addition)",
          "Equal radii → same circle: ab = ac ∧ center(a,α) ∧ center(a,β) ∧ on(b,α) ∧ on(c,β) → α = β",
          "Segment → circle: center(a,α) ∧ on(b,α) ∧ ac = ab → on(c,α)",
          "Radii equal: center(a,α) ∧ on(b,α) ∧ on(c,α) → ac = ab",
          "Segment < radius → inside: center(a,α) ∧ on(b,α) ∧ ac < ab → inside(c,α)",
          "Inside → segment < radius: center(a,α) ∧ on(b,α) ∧ inside(c,α) → ac < ab"]),
        ("Angle transfer", DIAGRAM_ANGLE_TRANSFER,
         ["Collinear zero angle: on(a,L) ∧ on(b,L) ∧ on(c,L) → ∠bac = 0 ∨ between(b,a,c)",
          "Zero angle → collinear: ∠bac = 0 → on(c,L)",
          "Zero angle → not between: ∠bac = 0 → ¬between(b,a,c)",
          "Angle addition: same-side decomposition → ∠bac = ∠bad + ∠dac",
          "Angle addition converse: ∠bac = ∠bad + ∠dac → same-side(b,d,M)",
          "Angle addition converse: ∠bac = ∠bad + ∠dac → same-side(c,d,L)",
          "Right angle: between(a,c,b) ∧ ∠acd = ∠dcb → ∠acd = right-angle",
          "Right angle converse: ∠acd = right-angle → ∠acd = ∠dcb",
          "Angle extension: supplementary ray → ∠bac = ∠b'ac'",
          "Parallel postulate: ∠abc + ∠bcd < 2·right → lines intersect",
          "Parallel postulate: intersection point same-side"]),
        ("Area transfer", DIAGRAM_AREA_TRANSFER,
         ["Zero area → collinear: △abc = 0 → on(c,L)",
          "Collinear → zero area: on(a,L) ∧ on(b,L) ∧ on(c,L) → △abc = 0",
          "Triangle area addition: between(a,c,b) → △acd + △dcb = △adb"]),
    ]
    for group_name, axioms, descs in _TRANSFER_GROUPS:
        for i, ax in enumerate(axioms):
            desc = descs[i] if i < len(descs) else f"{group_name} axiom {i+1}"
            rules.append(RuleInfo(
                name=f"{group_name} {i+1}",
                category="transfer",
                description=desc,
                section="§3.6",
            ))

    # ── Superposition (§3.7) ──────────────────────────────────────
    rules.append(RuleInfo(
        name="SAS Superposition",
        category="superposition",
        description="Side-Angle-Side: ab=de, ac=df, ∠bac=∠edf ⇒ bc=ef, ∠abc=∠def, ∠acb=∠dfe",
        section="§3.7",
    ))
    rules.append(RuleInfo(
        name="SSS Superposition",
        category="superposition",
        description="Side-Side-Side: ab=de, bc=ef, ac=df ⇒ ∠bac=∠edf, ∠abc=∠def, ∠acb=∠dfe",
        section="§3.7",
    ))

    # ── Structural rules ──────────────────────────────────────────
    rules.append(RuleInfo(
        name="Reit",
        category="structural",
        description="Reiteration: restate a previously established fact",
        section="§3.2",
    ))
    rules.append(RuleInfo(
        name="Assume",
        category="structural",
        description="Assume: open a subproof by assuming ¬φ (or φ)",
        section="§3.2",
    ))
    rules.append(RuleInfo(
        name="Reductio",
        category="structural",
        description="Reductio ad absurdum: derive φ from Assume ¬φ + contradiction (ψ ∧ ¬ψ)",
        section="§3.2",
    ))

    # ── Propositions (Book I) ─────────────────────────────────────
    for name, thm in E_THEOREM_LIBRARY.items():
        hyps = ", ".join(str(h) for h in thm.sequent.hypotheses) if thm.sequent.hypotheses else "—"
        concls = ", ".join(str(c) for c in thm.sequent.conclusions) if thm.sequent.conclusions else "—"
        sequent_str = f"{hyps} ⇒ {concls}"
        if len(sequent_str) > 100:
            sequent_str = sequent_str[:97] + "…"
        # Use the natural language statement as primary, sequent as secondary
        statement = getattr(thm, 'statement', '') or ''
        if statement:
            desc = f"{statement}\n{sequent_str}"
        else:
            desc = sequent_str
        rules.append(RuleInfo(
            name=name,
            category="proposition",
            description=desc,
            section="Book I",
        ))

    return rules


# ═══════════════════════════════════════════════════════════════════════
# Theorem catalogue access
# ═══════════════════════════════════════════════════════════════════════

def get_theorem(name: str) -> Optional[ETheorem]:
    """Retrieve a theorem by name from the library.

    Args:
        name: e.g. "Prop.I.1", "Prop.I.47"

    Returns:
        The ETheorem, or None if not found.
    """
    return E_THEOREM_LIBRARY.get(name)


def get_all_theorems() -> Dict[str, ETheorem]:
    """Return the entire theorem library."""
    return dict(E_THEOREM_LIBRARY)


def list_theorem_names() -> List[str]:
    """Return all theorem names in order."""
    return [f"Prop.I.{i}" for i in range(1, 49)]


# ═══════════════════════════════════════════════════════════════════════
# Formula parsing
# ═══════════════════════════════════════════════════════════════════════

def parse_e_formula(text: str, sort_ctx: Optional[Dict[str, Sort]] = None):
    """Parse a System E formula string into a list of literals.

    Returns a list of ``Literal`` objects or ``None`` on parse error.
    """
    from .e_parser import parse_literal_list, EParseError
    try:
        return parse_literal_list(text, sort_ctx)
    except EParseError:
        return None
