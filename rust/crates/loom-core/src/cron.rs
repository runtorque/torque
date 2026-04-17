//! Minimal 5-field cron expression parser.
//!
//! Direct port of `loom/cron.py`. Supports `*`, `*/N`, `N`, `N-M`, `N,M` in
//! each of the five fields (minute, hour, day-of-month, month, day-of-week),
//! with case-insensitive month (jan..dec) / weekday (sun..sat) names.
//!
//! No timezone support yet — an empty `tz` means UTC. (Matches Python's
//! default behavior; porting IANA tz support requires pulling in `chrono-tz`.)

use chrono::{DateTime, Datelike, Duration, TimeZone, Timelike, Utc};
use std::collections::BTreeSet;

const FIELD_RANGES: [(u32, u32); 5] = [
    (0, 59), // minute
    (0, 23), // hour
    (1, 31), // day of month
    (1, 12), // month
    (0, 6),  // day of week (0=Sun .. 6=Sat)
];

fn month_names() -> &'static [(&'static str, u32)] {
    &[
        ("jan", 1),
        ("feb", 2),
        ("mar", 3),
        ("apr", 4),
        ("may", 5),
        ("jun", 6),
        ("jul", 7),
        ("aug", 8),
        ("sep", 9),
        ("oct", 10),
        ("nov", 11),
        ("dec", 12),
    ]
}

fn dow_names() -> &'static [(&'static str, u32)] {
    &[
        ("sun", 0),
        ("mon", 1),
        ("tue", 2),
        ("wed", 3),
        ("thu", 4),
        ("fri", 5),
        ("sat", 6),
    ]
}

/// Substitute month / weekday names (case-insensitive) for their integer form.
fn substitute_names(part: &str, names: &[(&str, u32)]) -> String {
    let mut out = part.to_lowercase();
    for (name, val) in names {
        out = out.replace(name, &val.to_string());
    }
    out
}

fn parse_field(
    token: &str,
    lo: u32,
    hi: u32,
    names: Option<&[(&str, u32)]>,
) -> Result<BTreeSet<u32>, String> {
    let mut result = BTreeSet::new();
    for raw in token.split(',') {
        let mut part = raw.trim().to_string();
        if let Some(n) = names {
            part = substitute_names(&part, n);
        }
        if part == "*" {
            return Ok((lo..=hi).collect());
        }
        if let Some((base, step_s)) = part.split_once('/') {
            let step: u32 = step_s
                .parse()
                .map_err(|_| format!("Invalid step in: {token}"))?;
            if step == 0 {
                return Err(format!("Step must be positive: {token}"));
            }
            if base == "*" {
                for v in (lo..=hi).step_by(step as usize) {
                    result.insert(v);
                }
            } else if let Some((a, b)) = base.split_once('-') {
                let start: u32 = a.parse().map_err(|_| format!("Invalid: {token}"))?;
                let end: u32 = b.parse().map_err(|_| format!("Invalid: {token}"))?;
                for v in (start..=end).step_by(step as usize) {
                    result.insert(v);
                }
            } else {
                let start: u32 = base.parse().map_err(|_| format!("Invalid: {token}"))?;
                for v in (start..=hi).step_by(step as usize) {
                    result.insert(v);
                }
            }
        } else if let Some((a, b)) = part.split_once('-') {
            let start: u32 = a.parse().map_err(|_| format!("Invalid: {token}"))?;
            let end: u32 = b.parse().map_err(|_| format!("Invalid: {token}"))?;
            for v in start..=end {
                result.insert(v);
            }
        } else {
            let v: u32 = part.parse().map_err(|_| format!("Invalid: {token}"))?;
            result.insert(v);
        }
    }
    for v in &result {
        if *v < lo || *v > hi {
            return Err(format!("Value {v} out of range [{lo}-{hi}] in: {token}"));
        }
    }
    Ok(result)
}

#[derive(Debug, Clone)]
pub struct CronSpec {
    pub minutes: BTreeSet<u32>,
    pub hours: BTreeSet<u32>,
    pub doms: BTreeSet<u32>,
    pub months: BTreeSet<u32>,
    pub dows: BTreeSet<u32>,
}

pub fn parse_cron(expr: &str) -> Result<CronSpec, String> {
    let fields: Vec<&str> = expr.split_whitespace().collect();
    if fields.len() != 5 {
        return Err(format!(
            "Expected 5 fields, got {}: {:?}",
            fields.len(),
            expr
        ));
    }
    let names_maps: [Option<&[(&str, u32)]>; 5] =
        [None, None, None, Some(month_names()), Some(dow_names())];
    Ok(CronSpec {
        minutes: parse_field(fields[0], FIELD_RANGES[0].0, FIELD_RANGES[0].1, names_maps[0])?,
        hours: parse_field(fields[1], FIELD_RANGES[1].0, FIELD_RANGES[1].1, names_maps[1])?,
        doms: parse_field(fields[2], FIELD_RANGES[2].0, FIELD_RANGES[2].1, names_maps[2])?,
        months: parse_field(fields[3], FIELD_RANGES[3].0, FIELD_RANGES[3].1, names_maps[3])?,
        dows: parse_field(fields[4], FIELD_RANGES[4].0, FIELD_RANGES[4].1, names_maps[4])?,
    })
}

/// Compute the next UTC fire time after `after` for `expr`.
///
/// `tz` is currently ignored (treated as UTC) — see module doc.
pub fn next_run(expr: &str, after: DateTime<Utc>, _tz: &str) -> Result<DateTime<Utc>, String> {
    let spec = parse_cron(expr)?;
    // Advance to the next minute boundary.
    let mut dt = after
        .with_second(0)
        .and_then(|d| d.with_nanosecond(0))
        .ok_or_else(|| "time arithmetic failed".to_string())?
        + Duration::minutes(1);

    // 4 years of minutes as generous upper bound (DST isn't modeled — UTC only).
    let max_iter = 366u64 * 24 * 60 * 4;
    for _ in 0..max_iter {
        if !spec.months.contains(&dt.month()) {
            // Skip to first day of next valid month.
            dt = if dt.month() == 12 {
                Utc.with_ymd_and_hms(dt.year() + 1, 1, 1, 0, 0, 0)
                    .single()
                    .ok_or_else(|| "month rollover failed".to_string())?
            } else {
                Utc.with_ymd_and_hms(dt.year(), dt.month() + 1, 1, 0, 0, 0)
                    .single()
                    .ok_or_else(|| "month advance failed".to_string())?
            };
            continue;
        }
        // Python uses: isoweekday() % 7 → Sun=0..Sat=6.
        let dow_py = dt.weekday().num_days_from_sunday(); // Sun=0..Sat=6
        if !spec.doms.contains(&dt.day()) || !spec.dows.contains(&dow_py) {
            let mut next = dt
                .with_hour(0)
                .and_then(|d| d.with_minute(0))
                .ok_or_else(|| "day advance failed".to_string())?;
            next += Duration::days(1);
            dt = next;
            continue;
        }
        if !spec.hours.contains(&dt.hour()) {
            let mut next = dt
                .with_minute(0)
                .ok_or_else(|| "hour advance failed".to_string())?;
            next += Duration::hours(1);
            dt = next;
            continue;
        }
        if !spec.minutes.contains(&dt.minute()) {
            dt += Duration::minutes(1);
            continue;
        }
        return Ok(dt);
    }
    Err(format!("No matching time found within 4 years for: {expr:?}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn star_parses_all_values() {
        let spec = parse_cron("* * * * *").unwrap();
        assert_eq!(spec.minutes.len(), 60);
        assert_eq!(spec.hours.len(), 24);
        assert_eq!(spec.doms.len(), 31);
        assert_eq!(spec.months.len(), 12);
        assert_eq!(spec.dows.len(), 7);
    }

    #[test]
    fn step_every_five_minutes() {
        let spec = parse_cron("*/5 * * * *").unwrap();
        assert!(spec.minutes.contains(&0));
        assert!(spec.minutes.contains(&5));
        assert!(spec.minutes.contains(&55));
        assert_eq!(spec.minutes.len(), 12);
    }

    #[test]
    fn range_and_list() {
        let spec = parse_cron("0,30 9-17 * * mon-fri").unwrap();
        assert_eq!(spec.minutes.iter().copied().collect::<Vec<_>>(), vec![0, 30]);
        assert_eq!(spec.hours.len(), 9);
        assert_eq!(spec.dows.len(), 5);
    }

    #[test]
    fn rejects_wrong_field_count() {
        assert!(parse_cron("* * * *").is_err());
        assert!(parse_cron("* * * * * *").is_err());
    }

    #[test]
    fn next_run_hourly() {
        let start = Utc.with_ymd_and_hms(2026, 4, 17, 10, 30, 0).unwrap();
        let next = next_run("0 * * * *", start, "").unwrap();
        assert_eq!(next, Utc.with_ymd_and_hms(2026, 4, 17, 11, 0, 0).unwrap());
    }

    #[test]
    fn next_run_daily_at_midnight() {
        let start = Utc.with_ymd_and_hms(2026, 4, 17, 23, 59, 0).unwrap();
        let next = next_run("0 0 * * *", start, "").unwrap();
        assert_eq!(next, Utc.with_ymd_and_hms(2026, 4, 18, 0, 0, 0).unwrap());
    }

    #[test]
    fn next_run_weekday_only() {
        // Mon-Fri at 09:00. 2026-04-17 is a Friday; next hit should be Mon 20.
        let start = Utc.with_ymd_and_hms(2026, 4, 17, 10, 0, 0).unwrap();
        let next = next_run("0 9 * * mon-fri", start, "").unwrap();
        assert_eq!(next, Utc.with_ymd_and_hms(2026, 4, 20, 9, 0, 0).unwrap());
    }
}
