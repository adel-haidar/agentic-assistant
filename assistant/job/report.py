from assistant.job.models import RunReport


def format_report(report: RunReport) -> str:
    rejection_summary = "  ".join(
        f"({k}: {v})" for k, v in report.hard_rejected_by_reason.items()
    )
    lines = [
        "─── RUN SUMMARY ──────────────────────────────────────────────────",
        f"Run time:       {report.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"Platforms:      {', '.join(report.platforms) if report.platforms else 'none'}",
        f"Countries:      {' | '.join(report.countries)}",
        f"Raw results:    {report.raw_count} listings found",
        f"After dedup:    {report.dedup_count} unique listings",
        f"Hard rejected:  {report.hard_rejected_count}  {rejection_summary}",
        f"Scored:         {report.scored_count} listings evaluated",
        f"Saved to DB:    {report.db_saved_this_run} matches  "
        f"(Strong: {len(report.strong_matches)} | Good: {len(report.good_matches)})",
        "",
    ]

    if report.strong_matches:
        lines.append("─── STRONG MATCHES (score ≥ 70) ──────────────────────────────────")
        for i, sm in enumerate(report.strong_matches, 1):
            flags = ", ".join(sm.result.positive_flags) if sm.result.positive_flags else "—"
            lines += [
                f"[#{i}] {sm.listing.title} — {sm.listing.company}",
                f"     Location:  {sm.listing.location}, {sm.listing.country} | {sm.result.remote_type}",
                f"     Salary:    {sm.listing.salary_raw or 'not disclosed'}",
                f"     Score:     {sm.result.score}/100  |  Flags: {flags}",
                f"     URL:       {sm.listing.job_url}",
                f"     Summary:   {sm.result.ai_summary or 'N/A'}",
                f"     DB row:    #{sm.db_id}",
                "",
            ]

    if report.good_matches:
        lines.append("─── GOOD MATCHES (score 50–69) ───────────────────────────────────")
        for i, sm in enumerate(report.good_matches, 1):
            lines += [
                f"[#{i}] {sm.listing.title} — {sm.listing.company}",
                f"     {sm.listing.location}, {sm.listing.country} | Score: {sm.result.score}/100 | {sm.result.remote_type}",
                f"     {sm.listing.job_url}",
                "",
            ]

    if report.rejection_log:
        lines.append("─── REJECTION LOG (top 5 per reason) ────────────────────────────")
        for reason, entries in report.rejection_log.items():
            lines.append(f"{reason} ({len(entries)}):")
            for entry in entries[:5]:
                lines.append(f"  {entry}")
        lines.append("")

    lines += [
        "─── DB STATUS ────────────────────────────────────────────────────",
        f"Saved this run:  {report.db_saved_this_run}",
        f"Cumulative:      {report.db_cumulative}",
        "Review query:",
        "  SELECT title, company, location, match_score, job_url, match_tier",
        "  FROM job_matches WHERE status = 'new'",
        "  ORDER BY match_score DESC, run_timestamp DESC;",
    ]

    return "\n".join(lines)
