/* The newsletter / feed view simulator.
 *
 * Everything here runs in the browser against the published feeds. Three things are
 * deliberate and worth knowing before changing any of it.
 *
 * 1. There is no list of sources in this file. The feeds, their labels and their declared
 *    purposes all come from status.json, so a thirteenth source appears here the moment it
 *    is registered. The two predecessor repositories ship byte-identical copies of their
 *    simulator with the department baked in, and that is exactly how a shared file comes to
 *    be maintained twice and drift.
 *
 * 2. Times are compared as naive local strings, never as Date objects. The feeds publish
 *    `startTime` as the event's own wall clock in its own timezone, and the edition window
 *    is also wall-clock. Parsing either into a Date would silently apply the *viewer's*
 *    timezone, so an editor in California would see a different edition from one in
 *    Princeton. String comparison on `YYYY-MM-DDTHH:MM:SS` sorts and bounds correctly and
 *    cannot do that.
 *
 * 3. The edition rule lives in `resolveEdition` and nowhere else. It implements the
 *    standard weekly schedule only -- no holiday shifts, no skipped weeks. A shifted
 *    edition is reproduced by overriding the publication date, which the page says.
 */
(function () {
  "use strict";

  var DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  var MONTHS = ["January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December"];
  var STAMP_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/;
  var DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

  /* ------------------------------------------------------------------ dates ------- */

  /* Date arithmetic done in UTC on purpose. These are calendar dates, not instants: a
     local-midnight Date shifts across a DST boundary and can land on the wrong day. */
  function toUTC(dateText) {
    var p = dateText.split("-");
    return Date.UTC(+p[0], +p[1] - 1, +p[2]);
  }

  function fromUTC(ms) {
    var d = new Date(ms);
    return d.toISOString().slice(0, 10);
  }

  function addDays(dateText, n) {
    return fromUTC(toUTC(dateText) + n * 86400000);
  }

  function weekdayOf(dateText) {
    return new Date(toUTC(dateText)).getUTCDay();
  }

  function weekdayName(dateText) {
    return DAY_NAMES[weekdayOf(dateText)];
  }

  /** The Monday of the week containing `dateText`. */
  function weekStartFor(dateText) {
    var day = weekdayOf(dateText);
    return addDays(dateText, day === 0 ? -6 : 1 - day);
  }

  function normalizeTime(text, fallback) {
    var value = (text || "").trim() || fallback;
    return value.length === 5 ? value + ":00" : value;
  }

  function prettyDate(dateText) {
    var p = dateText.split("-");
    return weekdayName(dateText) + ", " + MONTHS[+p[1] - 1] + " " + +p[2];
  }

  function prettyStamp(stampText) {
    if (!STAMP_RE.test(stampText || "")) return stampText || "";
    return prettyDate(stampText.slice(0, 10)) + ", " + prettyClock(stampText);
  }

  /** `12:15 p.m.`, the way the newsletter writes it. */
  function prettyClock(stampText) {
    var hh = +stampText.slice(11, 13);
    var mm = stampText.slice(14, 16);
    var suffix = hh < 12 ? "a.m." : "p.m.";
    var hour = hh % 12 === 0 ? 12 : hh % 12;
    return hour + ":" + mm + " " + suffix;
  }

  function hoursBetween(a, b) {
    if (!STAMP_RE.test(a) || !STAMP_RE.test(b)) return null;
    var ms = function (s) {
      return Date.UTC(+s.slice(0, 4), +s.slice(5, 7) - 1, +s.slice(8, 10),
                      +s.slice(11, 13), +s.slice(14, 16), +s.slice(17, 19));
    };
    return (ms(b) - ms(a)) / 3600000;
  }

  /* ---------------------------------------------------------------- the edition --- */

  /**
   * The schedule a purpose that declares none falls back to.
   *
   * The engineering one, because it was the only publication when this page was written
   * and its behaviour is pinned by a differential test against a real edition. A purpose
   * declaring its own schedule in config overrides every field of this.
   */
  var DEFAULT_SCHEDULE = {
    publication: { weekday: "MON", time: "12:00" },
    deadline: { anchor: "week_start", offset_days: -6, time: "12:00" },
    coverage: {
      start: { anchor: "publication" },
      end: { anchor: "week_start", offset_days: 6 }
    }
  };

  /**
   * The layout used when nothing has chosen one.
   *
   * Named here so the pure functions work under Node with no manifest, and asserted
   * against `config/sources.yaml`'s declared default by test -- the config is the source
   * of truth, and this is the copy that has to agree with it.
   */
  var DEFAULT_TEMPLATE = "day-grouped";

  var WEEKDAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];

  /** Days from the Monday of a week to the named weekday. */
  function weekdayOffset(name) {
    var at = WEEKDAYS.indexOf(String(name || "MON").toUpperCase());
    return at === -1 ? 0 : at;
  }

  /**
   * Resolve one anchored bound to a date.
   *
   * Three anchors, and the third is why this exists: `next_week_start` is the Monday
   * *after* the publication's own week, which is what a Thursday edition covering "next
   * week's events" needs. `publication` follows a shifted publication date; `week_start`
   * stays pinned to its week.
   */
  function anchorDate(node, edition) {
    var anchor = (node && node.anchor) || "publication";
    var base =
      anchor === "week_start" ? edition.weekStart
      : anchor === "next_week_start" ? addDays(edition.weekStart, 7)
      : edition.publicationDate;
    return addDays(base, (node && node.offset_days) || 0);
  }

  /**
   * The publication date of the next edition of this publication that has not gone out.
   *
   * Not simply this week's Monday, which is what the reset used to offer: on a Saturday
   * that is an edition published five days ago covering a week that ends tomorrow. The
   * useful default is the edition somebody is composing, which is the next one to publish.
   *
   * The weekday comes from the schedule, so selecting the DAIS newsletter resets to the
   * next *Thursday* rather than the next Monday.
   */
  function nextEditionDate(nowStamp, schedule) {
    var plan = schedule || DEFAULT_SCHEDULE;
    var offset = weekdayOffset(plan.publication && plan.publication.weekday);
    var publicationDay = addDays(weekStartFor(nowStamp.slice(0, 10)), offset);
    var publishesAt =
      publicationDay + "T" + normalizeTime(plan.publication && plan.publication.time, "12:00:00");
    return nowStamp >= publishesAt ? addDays(publicationDay, 7) : publicationDay;
  }

  /** The deadline the standard schedule implies: the Tuesday before the week. */
  function defaultDeadlineDate(publicationDate) {
    return addDays(weekStartFor(publicationDate), -6);
  }

  /**
   * Resolve one edition from the controls.
   *
   * Coverage start follows a publication shift while coverage end stays pinned to the
   * week. That mixed anchoring looks like an oversight and is not: it is what makes a
   * Tuesday-published week cover Tuesday-to-Sunday rather than running a day past the
   * week it belongs to.
   */
  function resolveEdition(input) {
    if (!DATE_RE.test(input.publicationDate || "")) {
      throw new Error("Pick a publication date.");
    }
    var plan = input.schedule || DEFAULT_SCHEDULE;
    var publicationDate = input.publicationDate;
    var weekStart = weekStartFor(publicationDate);
    var edition = { weekStart: weekStart, publicationDate: publicationDate };

    /* A publication has no deadline unless its schedule declares one. DaIS states none --
       its edition says only to send an email -- and inventing one would put a date in
       front of an editor that nobody agreed to. */
    var deadlineAt = "";
    if (plan.deadline) {
      var deadlineDate = input.deadlineDate || anchorDate(plan.deadline, edition);
      if (!DATE_RE.test(deadlineDate)) throw new Error("The deadline date is not a date.");
      deadlineAt =
        deadlineDate + "T" + normalizeTime(input.deadlineTime || plan.deadline.time, "12:00:00");
    }

    var start = anchorDate(plan.coverage.start, edition);
    var end = anchorDate(plan.coverage.end, edition);
    if (end < start) throw new Error("The coverage window ends before it starts.");

    return {
      id: weekStart,
      weekStart: weekStart,
      publicationDate: publicationDate,
      publicationAt:
        publicationDate + "T" +
        normalizeTime(input.publicationTime || plan.publication.time, "12:00:00"),
      deadlineAt: deadlineAt,
      coverageStart: start + "T00:00:00",
      coverageEnd: end + "T23:59:59",
      /* Shifted from the weekday its own schedule names, not from Monday -- a Thursday
         publication is not a shifted Monday one. */
      shifted: publicationDate !== addDays(weekStart, weekdayOffset(plan.publication.weekday))
    };
  }

  function phaseAt(nowStamp, edition) {
    if (!STAMP_RE.test(nowStamp || "")) return null;
    if (nowStamp >= edition.publicationAt) return "published";
    // No deadline means no closed phase: an edition with no stated cut-off is open until
    // it publishes, which is what DaIS's "send us an email" amounts to.
    if (edition.deadlineAt && nowStamp >= edition.deadlineAt) return "closed";
    return "open";
  }

  /* Inclusive at both ends. */
  function inWindow(startTime, edition) {
    if (!STAMP_RE.test(startTime || "")) return false;
    return startTime >= edition.coverageStart && startTime <= edition.coverageEnd;
  }

  /* ------------------------------------------------------------------- events ----- */

  function speakerText(event) {
    var list = Array.isArray(event.speakers) ? event.speakers : [];
    var parts = list.map(function (s) {
      var name = (s && s.name ? s.name : "").trim();
      var aff = (s && s.affiliation ? s.affiliation : "").trim();
      return name && aff ? name + ", " + aff : name || aff;
    }).filter(Boolean);
    if (parts.length) return parts;
    var scalar = (event.speaker || "").trim();
    return scalar ? [scalar] : [];
  }

  /** The opening paragraph of a body of text, capped so a listing stays scannable. */
  function firstParagraph(text, limit) {
    var first = String(text).split(/\n{2,}/)[0].trim();
    /* Content opening with a labelled field is the event page's structured metadata, not
       prose -- `nam`'s reads "Speaker: Taylor Webb, Department of Psychology…Talk
       Abstract:". Printing it as a description puts a field name in the middle of a
       sentence, so it is omitted instead. An editor writing their own blurb is the
       correct outcome here; inventing one from a label is not. */
    if (/^[A-Z][A-Za-z ]{0,24}:/.test(first)) return "";
    var cap = limit || 420;
    if (first.length <= cap) return first;
    var cut = first.slice(0, cap);
    var lastStop = cut.lastIndexOf(". ");
    return lastStop > cap / 2 ? cut.slice(0, lastStop + 1) : cut.trimEnd() + "…";
  }

  function speakerNames(event) {
    var list = Array.isArray(event.speakers) ? event.speakers : [];
    return list.map(function (s) {
      return (s && s.name ? s.name : "").trim();
    }).filter(Boolean);
  }

  function locationText(event) {
    var loc = event.location;
    if (!loc || typeof loc !== "object") return "";
    var name = (loc.name || "").trim();
    var detail = (loc.detail || "").trim();
    if (!name) return detail;
    if (!detail) return name;
    /* The newsletter writes "Sherrerd Hall, Room 306". Only say "Room" when the detail
       actually looks like one -- some feeds put a floor or a building wing here. */
    return /^[A-Za-z]?\d{1,4}[A-Za-z]?$/.test(detail)
      ? name + ", Room " + detail
      : name + ", " + detail;
  }

  /** `Friend 006` -- venue and room with nothing inserted between them. */
  function plainLocation(event) {
    var loc = event.location;
    if (!loc || typeof loc !== "object") return "";
    var name = (loc.name || "").trim();
    var detail = (loc.detail || "").trim();
    if (!name) return detail;
    if (!detail) return name;
    return /^[A-Za-z]?\d{1,4}[A-Za-z]?$/.test(detail)
      ? name + " " + detail
      : name + ", " + detail;
  }

  function decorate(event, feeds) {
    var sources = Array.isArray(event.sources) ? event.sources : [];
    return {
      raw: event,
      guid: event.guid || "",
      startTime: event.startTime || "",
      // Needed for the inline template's "4:30 — 6 p.m." range; the day-grouped one
      // prints only a start, which is why its absence went unnoticed.
      endTime: event.endTime || "",
      title: (event.title || "").trim(),
      /* A comma-joined multi-value series arrives as "A,B" because that is how the feed
         publishes it. Spacing it is presentation for a human composing a listing, not a
         change to what the feed says. */
      series: (event.series || "").trim().replace(/,(?=\S)/g, ", "),
      /* The DAIS edition prints a blurb for some events; the engineering one never does.
         Trimmed to a paragraph, because `content` can run to a full abstract and an
         editor wants the opening of it, not all of it. */
      description: firstParagraph(event.content || ""),
      speakers: speakerText(event),
      /* Bare names, kept alongside the display strings. The two feeds listing the same
         talk agreed on "Rafael Gomez-Bombarelli" and disagreed on whether to include his
         institution, so a duplicate check on the display string misses it. */
      names: speakerNames(event),
      location: locationText(event),
      /* The same venue without the word "Room". The DAIS edition writes "in Friend 006"
         and "in Computer Science Building 105"; the engineering one writes
         "Sherrerd Hall, Room 306". Two publications, two conventions, one field each. */
      place: plainLocation(event),
      urlRef: event.urlRef || "",
      sources: sources,
      /* Each sponsor keeps its own site, taken from the feed rather than from the
         event's URL: a talk two units both list carries one URL and needs two links. */
      sponsors: sources.map(function (slug) {
        var record = feeds[slug] || {};
        return { label: record.label || slug, home: record.home || "" };
      }),
      purposes: Array.isArray(event.purposes) ? event.purposes : [],
      /* Our feeds always carry provenance, so unlike the predecessors' simulator there is
         no need to guess placeholder status from the shape of the title. */
      placeholder: event.titleIsPlaceholder === true,
      malformedStart: !STAMP_RE.test(event.startTime || "")
    };
  }

  function fold(text) {
    return text.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  }

  /**
   * How ready one event is to go into an edition, and what is missing.
   *
   * Three tiers, graded by what an editor would have to do about it:
   *
   * - `check`  something is wrong or unread -- cancelled, a mapping conflict, a category
   *            nothing recognises. Rare, and exactly when somebody needs telling.
   * - `fix`    publishable but not sendable as it stands: the title is a synthesized
   *            placeholder, or there is no location to print.
   * - `ready`  nothing to chase.
   *
   * A missing **speaker** deliberately does not force `fix`. Two events in five have
   * none, and most legitimately so -- a reading group or a workshop has no one speaker.
   * Grading on it would put two fifths of an edition in amber, and a signal that fires
   * that often is one everybody learns to scroll past. It is still reported as a gap.
   */
  function readiness(item) {
    var raw = item.raw || {};
    var wrong = [];
    if (raw.cancelled) wrong.push("cancelled");
    if (raw.mappingConflict) wrong.push("the mapping conflicted");
    if ((raw.unmappedTags || []).length) {
      wrong.push("a category nothing recognises: " + raw.unmappedTags.join(", "));
    }

    var blocking = [];
    if (item.placeholder) blocking.push("no title announced yet");
    if (!item.location) blocking.push("no location");

    var gaps = [];
    if (!item.speakers.length) gaps.push("no speaker");
    if (!item.series) gaps.push("no series");
    if (!(raw.content || "").trim()) gaps.push("no description");

    var state = wrong.length ? "check" : blocking.length ? "fix" : "ready";
    return {
      state: state,
      label: state === "check" ? "check" : state === "fix" ? "fix first" : "ready",
      reasons: wrong.concat(blocking),
      gaps: gaps
    };
  }


  /**
   * A key two feeds would collide on if they are listing the same talk.
   *
   * Keyed on the speaker rather than the title when the title is a placeholder, because a
   * placeholder is *synthesized from the source's own template* -- the same talk comes out
   * as "A PMI/PCCM Seminar Series Talk by Rafael Gomez-Bombarelli" from one unit and
   * "An AI for Accelerating Invention Talk by Rafael Gomez-Bombarelli" from another.
   * Comparing those titles finds no duplicate where there plainly is one, which is how
   * this went unnoticed until the simulator was pointed at a real week.
   */
  function collisionKey(item) {
    var identity = item.placeholder && item.names.length
      ? fold(item.names[0])
      : fold(item.title);
    return item.startTime + "|" + identity;
  }

  function partition(events, edition, purpose, feeds) {
    var included = [];
    var excluded = [];
    var malformed = [];

    events.forEach(function (event) {
      var item = decorate(event, feeds);
      if (item.malformedStart) {
        malformed.push(item);
        return;
      }
      if (purpose && item.purposes.indexOf(purpose) === -1) {
        item.reason = "Does not serve " + purpose;
        excluded.push(item);
        return;
      }
      if (inWindow(item.startTime, edition)) {
        included.push(item);
      } else {
        item.reason = item.startTime < edition.coverageStart
          ? "Starts before the window" : "Starts after the window";
        excluded.push(item);
      }
    });

    var byStart = function (a, b) {
      if (a.startTime !== b.startTime) return a.startTime < b.startTime ? -1 : 1;
      return a.title < b.title ? -1 : a.title > b.title ? 1 : 0;
    };
    included.sort(byStart);
    excluded.sort(byStart);

    /* Grouped rather than counted, so the note can show *which* events look alike. A
       bare number tells an editor there is a duplicate and not where to look for it. */
    var byKey = {};
    included.forEach(function (item) {
      var key = collisionKey(item);
      byKey[key] = byKey[key] || [];
      byKey[key].push(item);
    });
    var groups = Object.keys(byKey)
      .map(function (key) { return byKey[key]; })
      .filter(function (group) { return group.length > 1; });

    return {
      included: included,
      excluded: excluded,
      malformed: malformed,
      collisionGroups: groups,
      collisions: groups.reduce(function (n, g) { return n + g.length - 1; }, 0),
      placeholders: included.filter(function (i) { return i.placeholder; }).length
    };
  }

  /* -------------------------------------------------------------- the listing ----- */

  /**
   * Group the edition by day, as the editors compose it.
   *
   * Only days with events get a heading, which is what their own file does -- the
   * September 7 edition carries Tuesday and Wednesday and nothing else.
   */
  function groupByDay(items) {
    var days = [];
    var index = {};
    items.forEach(function (item) {
      var day = item.startTime.slice(0, 10);
      if (!index[day]) {
        index[day] = { day: day, items: [] };
        days.push(index[day]);
      }
      index[day].items.push(item);
    });
    return days;
  }

  function plural(word, list) {
    return list.length > 1 ? word + "s:" : word + ":";
  }

  //: Month names as the DAIS edition abbreviates them -- "Sept. 28", not "September 28".
  //: September is "Sept." rather than "Sep.", which is the Princeton house style and what
  //: their own edition writes.
  var SHORT_MONTHS = ["Jan.", "Feb.", "March", "April", "May", "June",
                      "July", "Aug.", "Sept.", "Oct.", "Nov.", "Dec."];

  /** `Monday, Sept. 28` -- the inline date form. */
  function shortDate(dateText) {
    var parts = dateText.split("-");
    return weekdayName(dateText) + ", " + SHORT_MONTHS[+parts[1] - 1] + " " + +parts[2];
  }

  /** `4:30` / `6` -- a clock with no meridiem and no bare `:00`. */
  function bareClock(stampText) {
    var hh = +stampText.slice(11, 13);
    var mm = stampText.slice(14, 16);
    var hour = hh % 12 === 0 ? 12 : hh % 12;
    return mm === "00" ? String(hour) : hour + ":" + mm;
  }

  function meridiem(stampText) {
    return +stampText.slice(11, 13) < 12 ? "a.m." : "p.m.";
  }

  /**
   * True when a span runs midnight to a later midnight.
   *
   * The model carries no all-day flag -- an ICS `DTSTART;VALUE=DATE` arrives as
   * `T00:00:00` like any other time -- so it is read off the stamps rather than trusted
   * from a field that does not exist. `robotics`' Northeast Robotics Colloquium is the
   * live case: 3 October 00:00 to 4 October 00:00.
   */
  function spansWholeDays(startTime, endTime) {
    return STAMP_RE.test(endTime || "") &&
      startTime.slice(11) === "00:00:00" &&
      endTime.slice(11) === "00:00:00" &&
      endTime.slice(0, 10) > startTime.slice(0, 10);
  }

  /**
   * `4:30 — 6 p.m.` -- a time range the way the DAIS edition writes one.
   *
   * The meridiem is stated once when both ends share it and twice when they do not, which
   * is what makes `11 a.m. — 12 p.m.` read correctly while `4:30 — 6 p.m.` stays short.
   * `:00` is dropped throughout: their edition writes `6 p.m.`, never `6:00 p.m.`
   *
   * An all-day event is written `All day` rather than run through that arithmetic, which
   * would otherwise produce `12 — 12 a.m.` -- a zero-length midnight event, and a claim
   * the feed does not make.
   */
  function timeRange(startTime, endTime) {
    if (spansWholeDays(startTime, endTime)) return "All day";
    var open = bareClock(startTime);
    if (!STAMP_RE.test(endTime || "")) return open + " " + meridiem(startTime);
    var sameHalf = meridiem(startTime) === meridiem(endTime);
    return sameHalf
      ? open + " — " + bareClock(endTime) + " " + meridiem(endTime)
      : open + " " + meridiem(startTime) + " — " + bareClock(endTime) + " " + meridiem(endTime);
  }

  /**
   * `4:30 — 6 p.m. Monday, Sept. 28, in Friend 006` -- the one line that replaces four.
   *
   * `location TBA` where the feed carries none. The engineering template omits the line
   * entirely in that case; DAIS writes the words, and the difference is per template
   * rather than an inconsistency.
   */
  function whenAndWhere(item) {
    return timeRange(item.startTime, item.endTime) + " " +
      shortDate(item.startTime.slice(0, 10)) + ", " +
      (item.place ? "in " + item.place : "location TBA");
  }

  /**
   * The listing as an element tree, used for the preview and as the copied markup.
   *
   * `doc` is a parameter rather than the global `document` so this can be rendered under a
   * stub and asserted -- the export is the part an editor actually pastes into Mailchimp,
   * so it is the last thing that should go unchecked.
   */
  function buildListing(edition, items, doc, template) {
    return template === "inline-date"
      ? buildInlineListing(edition, items, doc)
      : buildDayGroupedListing(edition, items, doc);
  }

  /**
   * The DAIS shape: chronological, no day headings, one when-and-where line.
   *
   * Transcribed from the edition of 24 September 2026. Its hand-authored original is
   * inconsistent about a comma after the meridiem, about including the year, and about en
   * versus em dash -- and one of its "Learn More" links points at the wrong event. This
   * picks one form and holds it, which is the whole point of generating it.
   */
  function buildInlineListing(edition, items, doc) {
    var root = doc.createElement("div");
    var heading = doc.createElement("h2");
    heading.className = "edition";
    heading.textContent = "Next Week’s Events";
    root.appendChild(heading);

    if (!items.length) {
      var empty = doc.createElement("p");
      empty.className = "empty";
      empty.textContent = "No events fall in this edition.";
      root.appendChild(empty);
      return root;
    }

    items.forEach(function (item, index) {
      /* A dotted rule between events, as their edition has -- between, not after, so the
         listing does not end on a trailing line an editor has to delete. */
      if (index) {
        var rule = doc.createElement("hr");
        rule.className = "ev-rule";
        root.appendChild(rule);
      }

      var block = doc.createElement("div");
      block.className = "ev";

      /* Grouped the way their own email is, rather than as one run of paragraphs.
       *
       * Mailchimp builds that edition from text blocks with 12px of padding top and
       * bottom: the title and its attribution share one block, the speaker and the
       * when-and-where line share another, and the blurb and the button are blocks of
       * their own. So lines inside a group sit tight on the leading and the gaps fall
       * between groups.
       *
       * Doing the same here is also what makes the spacing survive a field being absent.
       * Margins on individual paragraphs cannot: three of the eight entries carry no
       * attribution and three carry no speaker, and every combination would need its own
       * first-of-group rule. A sibling combinator would express it in one line and is no
       * use -- Outlook does not honour them, and `inlineStyles` keys on `tag.class`.
       */
      var head = doc.createElement("div");
      head.className = "ev-head";

      var title = doc.createElement("p");
      title.className = "ev-title";
      if (item.urlRef) {
        var link = doc.createElement("a");
        link.className = "ev-link";
        link.setAttribute("href", item.urlRef);
        link.textContent = item.title || "(no title)";
        title.appendChild(link);
      } else {
        title.textContent = item.title || "(no title)";
      }
      if (item.placeholder) {
        var flag = doc.createElement("span");
        flag.className = "placeholder-flag";
        flag.textContent = "  [no title announced yet — replace before sending]";
        title.appendChild(flag);
      }
      head.appendChild(title);

      /* One verb, generated. Their edition also says "Co-sponsored by" and "Presented
         by", but neither is in any feed and claiming one would be inventing a fact. */
      if (item.sponsors.length) {
        var hosted = doc.createElement("p");
        hosted.className = "ev-hosted";
        hosted.appendChild(doc.createTextNode("Hosted by "));
        item.sponsors.forEach(function (sponsor, index) {
          if (index) hosted.appendChild(doc.createTextNode(", "));
          if (sponsor.home) {
            var anchor = doc.createElement("a");
            anchor.className = "sponsor-link";
            anchor.setAttribute("href", sponsor.home);
            anchor.textContent = sponsor.label;
            hosted.appendChild(anchor);
          } else {
            var plain = doc.createElement("span");
            plain.className = "sep";
            plain.textContent = sponsor.label;
            hosted.appendChild(plain);
          }
        });
        head.appendChild(hosted);
      }
      block.appendChild(head);

      /* Before the speaker, as their edition orders it -- the blurb explains the series
         and the speaker belongs with the time and place, not adrift above a paragraph. */
      if (item.description) {
        var blurb = doc.createElement("p");
        blurb.className = "ev-blurb";
        blurb.textContent = item.description;
        block.appendChild(blurb);
      }

      var detail = doc.createElement("div");
      detail.className = "ev-detail";

      // Unlabelled, as their edition writes it: "Taylor Webb, Department of Psychology".
      if (item.speakers.length) {
        var who = doc.createElement("p");
        who.className = "ev-speaker";
        who.textContent = item.speakers.join("; ");
        detail.appendChild(who);
      }

      var when = doc.createElement("p");
      when.className = "ev-when";
      when.textContent = whenAndWhere(item);
      detail.appendChild(when);
      block.appendChild(detail);

      /* Their "Learn More" is a pink pill, so ours is too -- `ev-button` rather than
         `ev-link`, because an underlined black link inside a filled button reads as a
         mistake. The two classes are styled separately for that reason. */
      if (item.urlRef) {
        var more = doc.createElement("p");
        more.className = "ev-more";
        var moreLink = doc.createElement("a");
        moreLink.className = "ev-button";
        moreLink.setAttribute("href", item.urlRef);
        moreLink.textContent = "Learn More";
        more.appendChild(moreLink);
        block.appendChild(more);
      }

      root.appendChild(block);
    });
    return root;
  }

  function buildDayGroupedListing(edition, items, doc) {
    var root = doc.createElement("div");
    var heading = doc.createElement("h2");
    heading.className = "edition";
    heading.textContent = "Events Newsletter: " +
      prettyDate(edition.coverageStart.slice(0, 10)).replace(/^\w+, /, "") + " - " +
      prettyDate(edition.coverageEnd.slice(0, 10)).replace(/^\w+, /, "") + ", " +
      edition.coverageEnd.slice(0, 4);
    root.appendChild(heading);

    if (!items.length) {
      var empty = doc.createElement("p");
      empty.className = "empty";
      empty.textContent = "No events fall in this edition.";
      root.appendChild(empty);
      return root;
    }

    groupByDay(items).forEach(function (group) {
      var dayHead = doc.createElement("h3");
      dayHead.className = "day";
      dayHead.textContent = prettyDate(group.day);
      root.appendChild(dayHead);

      group.items.forEach(function (item) {
        var block = doc.createElement("div");
        block.className = "ev";

        var title = doc.createElement("p");
        title.className = "ev-title";
        /* The title links to the event's own page. An editor pasting this into Mailchimp
           would otherwise have to find and attach every link by hand, which is the part
           of composing an edition that takes the time. */
        if (item.urlRef) {
          var link = doc.createElement("a");
          link.className = "ev-link";
          link.setAttribute("href", item.urlRef);
          link.textContent = item.title || "(no title)";
          title.appendChild(link);
        } else {
          title.textContent = item.title || "(no title)";
        }
        if (item.placeholder) {
          var flag = doc.createElement("span");
          flag.className = "placeholder-flag";
          flag.textContent = "  [no title announced yet — replace before sending]";
          title.appendChild(flag);
        }
        block.appendChild(title);

        var time = doc.createElement("p");
        time.className = "ev-time";
        time.textContent = prettyClock(item.startTime);
        block.appendChild(time);

        var dl = doc.createElement("dl");
        dl.className = "ev-fields";

        var field = function (label) {
          var dt = doc.createElement("dt");
          dt.className = "ev-label";
          dt.textContent = label;
          dl.appendChild(dt);
          var dd = doc.createElement("dd");
          dd.className = "ev-value";
          dl.appendChild(dd);
          return dd;
        };
        var add = function (label, value) {
          if (value) field(label).textContent = value;
        };

        add(plural("Speaker", item.speakers), item.speakers.join("; "));

        /* Sponsors link to their own unit's site rather than to the event, so a merged
           talk credits each publisher with somewhere of its own to go. */
        if (item.sponsors.length) {
          var dd = field(plural("Sponsor", item.sponsors));
          item.sponsors.forEach(function (sponsor, index) {
            if (index) {
              var sep = doc.createElement("span");
              sep.className = "sep";
              sep.textContent = "; ";
              dd.appendChild(sep);
            }
            if (sponsor.home) {
              var anchor = doc.createElement("a");
              anchor.className = "sponsor-link";
              anchor.setAttribute("href", sponsor.home);
              anchor.textContent = sponsor.label;
              dd.appendChild(anchor);
            } else {
              var plain = doc.createElement("span");
              plain.className = "sep";
              plain.textContent = sponsor.label;
              dd.appendChild(plain);
            }
          });
        }

        add("Series:", item.series);
        add("Location:", item.location);
        block.appendChild(dl);

        root.appendChild(block);
      });
    });
    return root;
  }

  /* --------------------------------------------------------- the deadline event --- */

  var ZONE = "America/New_York";

  /**
   * The UTC instant for a wall-clock time in `zone`.
   *
   * The deadline is Eastern wall time and a calendar file needs an unambiguous instant,
   * so the offset has to be resolved for *that date* -- it is four hours in September and
   * five in December, and hardcoding either is wrong for half the year.
   *
   * Two passes: read back what the guessed instant shows in the zone, correct by the
   * difference, then confirm. The second pass is what handles a date near a transition,
   * where the first correction can land on the other side of it.
   */
  function zonedToUTC(stamp, zone) {
    var target = Date.UTC(
      +stamp.slice(0, 4), +stamp.slice(5, 7) - 1, +stamp.slice(8, 10),
      +stamp.slice(11, 13), +stamp.slice(14, 16), +stamp.slice(17, 19) || 0
    );
    var guess = target;
    for (var pass = 0; pass < 2; pass += 1) {
      guess = target - (shownAsUTC(guess, zone) - guess);
    }
    return new Date(guess);
  }

  /** What `instant` reads as on a clock in `zone`, expressed as a UTC timestamp. */
  function shownAsUTC(instant, zone) {
    var parts = new Intl.DateTimeFormat("en-US", {
      timeZone: zone, hour12: false,
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit"
    }).formatToParts(new Date(instant));
    var at = {};
    parts.forEach(function (part) { at[part.type] = part.value; });
    return Date.UTC(+at.year, +at.month - 1, +at.day,
                    +at.hour % 24, +at.minute, +at.second);
  }

  /** `20260901T160000Z`, the only stamp shape a calendar file and Google both accept. */
  function utcStamp(date) {
    return date.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
  }

  /**
   * End a sentence without doubling a full stop.
   *
   * Times here end in "p.m." and a sentence ending in one reads "12:00 p.m..", which
   * already had to be fixed once on the page itself.
   */
  function sentence(text) {
    return /\.$/.test(text) ? text : text + ".";
  }

  /** What the reminder says, so it is useful on its own a week later. */
  function deadlineEvent(edition, counts) {
    return {
      title: "Newsletter submissions close",
      start: zonedToUTC(edition.deadlineAt, ZONE),
      /* Half an hour. A zero-length event is legal and several clients render it oddly or
         drop it from an agenda view, which would defeat the point of adding it. */
      end: new Date(zonedToUTC(edition.deadlineAt, ZONE).getTime() + 1800000),
      details: [
        sentence("Submissions close for the edition publishing " +
          prettyStamp(edition.publicationAt)),
        sentence("That edition covers " + prettyDate(edition.coverageStart.slice(0, 10)) +
          " through " + prettyDate(edition.coverageEnd.slice(0, 10))),
        counts ? counts + " event(s) are in it as things stand." : "",
        "Late additions go to the editor by email."
      ].filter(Boolean).join("\n\n")
    };
  }

  /** A one-event calendar file. CRLF and folding per RFC 5545, as every client expects. */
  function deadlineIcs(edition, counts, url) {
    var event = deadlineEvent(edition, counts);
    var lines = [
      "BEGIN:VCALENDAR",
      "VERSION:2.0",
      "PRODID:-//pu-shd//upcoming newsletter simulator//EN",
      "CALSCALE:GREGORIAN",
      "BEGIN:VEVENT",
      /* Deterministic rather than random: adding the same deadline twice should update
         the one entry rather than leave a duplicate behind. */
      "UID:newsletter-deadline-" + edition.id + "@pu-shd.github.io",
      "DTSTAMP:" + utcStamp(event.start),
      "DTSTART:" + utcStamp(event.start),
      "DTEND:" + utcStamp(event.end),
      "SUMMARY:" + icsText(event.title),
      "DESCRIPTION:" + icsText(event.details + (url ? "\n\n" + url : "")),
      url ? "URL:" + icsText(url) : "",
      "BEGIN:VALARM",
      "TRIGGER:-PT24H",
      "ACTION:DISPLAY",
      "DESCRIPTION:" + icsText(event.title),
      "END:VALARM",
      "END:VEVENT",
      "END:VCALENDAR"
    ].filter(Boolean);
    return lines.map(foldLine).join("\r\n") + "\r\n";
  }

  /** RFC 5545 TEXT escaping: backslash first, or it would escape its own output. */
  function icsText(value) {
    return String(value)
      .replace(/\\/g, "\\\\")
      .replace(/;/g, "\\;")
      .replace(/,/g, "\\,")
      .replace(/\n/g, "\\n");
  }

  /** Fold at 75 octets onto continuation lines beginning with a space. */
  function foldLine(line) {
    if (line.length <= 75) return line;
    var out = line.slice(0, 75);
    var rest = line.slice(75);
    while (rest.length > 74) {
      out += "\r\n " + rest.slice(0, 74);
      rest = rest.slice(74);
    }
    return out + "\r\n " + rest;
  }

  function googleCalendarUrl(edition, counts, url) {
    var event = deadlineEvent(edition, counts);
    var params = new URLSearchParams({
      action: "TEMPLATE",
      text: event.title,
      dates: utcStamp(event.start) + "/" + utcStamp(event.end),
      details: event.details + (url ? "\n\n" + url : "")
    });
    return "https://calendar.google.com/calendar/render?" + params.toString();
  }

  /* ------------------------------------------------------------ export styling ---- */

  /**
   * How the exported listing is styled, by the tag and class the builder emits.
   *
   * Written for **email**, not for this site: no grid, no flex, no custom properties, no
   * class selectors relied on. Mailchimp and the clients it sends to strip `<style>` and
   * honour only `style=""` attributes, so anything that has to survive the trip is applied
   * per element. Where grid is dropped, a `dl` falls back to ordinary block flow -- label
   * on one line, value on the next -- which is how the editors' own edition already reads.
   *
   * The colours are literal rather than tokens for the same reason: the exported file
   * leaves this site, where nothing defines `--dim`.
   */
  /* The DaIS email's own type and colour, kept in one place because fourteen rules
     repeat them and a typo in one would be invisible against thirteen correct ones. */
  var DAIS_FAMILY = "'Helvetica Neue', Helvetica, Arial, Verdana, sans-serif";
  var DAIS_FONT = "font-family: " + DAIS_FAMILY + "; font-weight: 400; line-height: 1.5; ";
  var DAIS_CENTRED = " text-align: center; color: #000000;";

  var EXPORT_STYLES = {
    /* The Princeton Engineering shape: left-aligned, a serif edition heading, a ruled day
       heading, and the editors' own bold field labels. */
    "day-grouped": {
      "h2.edition":
        "font: 700 20px/1.3 Georgia, 'Times New Roman', serif; margin: 0 0 20px;",
      "h3.day": "font: 700 15px/1.3 Helvetica, Arial, sans-serif; margin: 28px 0 10px; "
        + "padding-bottom: 4px; border-bottom: 1px solid #d8d8d8;",
      "div.ev": "margin: 0 0 20px;",
      "p.ev-title": "font: 700 15px/1.4 Helvetica, Arial, sans-serif; margin: 0 0 2px;",
      "a.ev-link": "color: #17181c; text-decoration: underline;",
      "p.ev-time": "font: 400 14px/1.4 Helvetica, Arial, sans-serif; margin: 0 0 6px; "
        + "color: #555555;",
      "span.placeholder-flag": "font: 400 13px/1.4 Helvetica, Arial, sans-serif; "
        + "color: #9a6700;",
      "dl.ev-fields": "margin: 0; font: 400 14px/1.5 Helvetica, Arial, sans-serif;",
      "dt.ev-label": "font-weight: 700; margin: 0;",
      "dd.ev-value": "margin: 0 0 4px;",
      "a.sponsor-link": "color: #17181c; text-decoration: underline;",
      "span.sep": "color: inherit;",
      "p.empty": "font: 400 14px/1.5 Helvetica, Arial, sans-serif;"
    },

    /* The DaIS shape, measured from the issue of 24 September 2026 rather than designed.
       Everything centred -- their Mailchimp template sets `text-align: center` on every
       paragraph -- in Helvetica Neue at 16px/1.5, with a 22px normal-weight underlined
       title, an italic attribution, a dotted pink rule between events and a pink pill for
       the link. `#EC2770` is their accent, taken from the divider and the button in that
       email; it is written here as a literal for the same reason every other colour is,
       since the exported file leaves this site. */
    "inline-date": {
      "h2.edition": DAIS_FONT + "font-size: 26px; margin: 0 0 32px;" + DAIS_CENTRED,
      /* The measure, and the reason centred text needs one: a centred line is read from
         a ragged left edge, so it stays legible for far fewer characters than a flush
         one. `margin: auto` is what actually centres the column -- `text-align` alone
         centres the text inside a block that is still sitting wherever its width put it,
         which is how the preview came to look a little to the left of everything. */
      "div.ev": "max-width: 544px; margin: 0 auto 32px;" + DAIS_CENTRED,
      /* Groups. Tight inside, evenly spaced between -- 24px, which is the 12px of top
         and bottom padding Mailchimp gives each of their text blocks. */
      "div.ev-head": "margin: 0;" + DAIS_CENTRED,
      "div.ev-detail": "margin: 24px 0 0;" + DAIS_CENTRED,
      "p.ev-title": DAIS_FONT + "font-size: 22px; margin: 0;" + DAIS_CENTRED,
      "a.ev-link": "color: #000000; text-decoration: underline;",
      "p.ev-hosted": DAIS_FONT + "font-size: 16px; font-style: italic; margin: 0;"
        + DAIS_CENTRED,
      "p.ev-blurb": DAIS_FONT + "font-size: 16px; margin: 24px 0 0;" + DAIS_CENTRED,
      "p.ev-speaker": DAIS_FONT + "font-size: 16px; margin: 0;" + DAIS_CENTRED,
      "p.ev-when": DAIS_FONT + "font-size: 16px; margin: 0;" + DAIS_CENTRED,
      "p.ev-more": "margin: 24px 0 0;" + DAIS_CENTRED,
      /* `display: inline-block` with padding, not a table: a real Mailchimp button is a
         nested table, and pasting one in would fight the editor's own block structure. */
      "a.ev-button": "background-color: #EC2770; border: 2px solid #000000; "
        + "border-radius: 50px; color: #ffffff; display: inline-block; "
        + "font: 400 16px/1.2 " + DAIS_FAMILY + "; "
        + "padding: 14px 28px; text-align: center; text-decoration: none;",
      "span.placeholder-flag": DAIS_FONT + "font-size: 14px; color: #9a6700;",
      "a.sponsor-link": "color: #000000; text-decoration: underline;",
      "span.sep": "color: inherit;",
      /* Full width while the events are inset, as in their edition: the divider block
         has no side padding there and the text blocks have 24px of it. */
      "hr.ev-rule": "border: 0; border-top: 2px dotted #EC2770; margin: 0 0 32px;",
      "p.empty": DAIS_FONT + "font-size: 16px; margin: 0;" + DAIS_CENTRED
    }
  };

  /**
   * The wrapper the downloaded file puts the listing in, per layout.
   *
   * Not part of `EXPORT_STYLES`: every key there is a `tag.class` selector that
   * `inlineStyles` matches against the markup, and a key that named no element would
   * break the check that the two agree. The wrapper belongs to the document, not to the
   * listing -- the copied markup has none, because Mailchimp supplies its own.
   */
  var EXPORT_WRAPPERS = {
    "day-grouped": "max-width: 640px; margin: 24px auto; padding: 0 16px; color: #17181c;",
    /* Their template's own measure and colours, so opening the file looks like the
       email rather than like this site with the email pasted into it. */
    "inline-date": "max-width: 600px; margin: 24px auto; padding: 24px 16px; "
      + "background: #ffffff; color: #000000;"
  };

  /** The rules for one layout, falling back to the default rather than to nothing. */
  function stylesFor(template) {
    return EXPORT_STYLES[template] || EXPORT_STYLES[DEFAULT_TEMPLATE];
  }

  /** The same rules as a stylesheet, for the file when it is simply opened in a browser. */
  function exportStylesheet(template) {
    var rules = stylesFor(template);
    return Object.keys(rules).map(function (selector) {
      return selector + " { " + rules[selector] + " }";
    }).join("\n");
  }

  /**
   * Apply the rules above as `style` attributes on the markup we emit.
   *
   * A string rewrite rather than a walk over the DOM, deliberately. The alternative is to
   * traverse and mutate, and the tags and classes here are a closed set this file
   * generates itself -- there is no arbitrary HTML to parse, and no user content reaches
   * a tag name or an attribute. Doing it on the string also means the preview keeps using
   * the site's stylesheet and only the exported copy carries the weight.
   */
  /** Break the markup onto lines so a downloaded file can be read and diffed. */
  function readable(html) {
    return html
      .replace(/<h3 /g, "\n<h3 ")
      .replace(/<hr /g, "\n<hr ")
      .replace(/<div class="ev"/g, "\n<div class=\"ev\"")
      .replace(/<\/div>/g, "</div>\n")
      .trim();
  }

  function inlineStyles(html, template) {
    var rules = stylesFor(template);
    return Object.keys(rules).reduce(function (text, selector) {
      var parts = selector.split(".");
      // Matched on the class anywhere inside the opening tag, so an element that also
      // carries an href is styled regardless of which attribute the DOM serialized first.
      var pattern = new RegExp(
        "<" + parts[0] + '(\\s[^>]*class="' + parts[1] + '"[^>]*)>', "g"
      );
      return text.replace(pattern, function (_match, attrs) {
        return "<" + parts[0] + attrs + ' style="' + rules[selector] + '">';
      });
    }, html);
  }

  /**
   * The standalone document the download produces, built from the element on the page.
   *
   * A named function rather than a few lines inside a click handler, because a click
   * handler is the one place nothing can reach to test it -- and the first version of this
   * shipped broken, emitting a title and an empty body, precisely because it lived there.
   */
  function exportDocument(el, template) {
    var body = el ? el.innerHTML : "";
    if (!body) return "";
    var titleEl = el.querySelector("h2");
    return "<!doctype html>\n<meta charset=\"utf-8\">\n<title>" +
      (titleEl ? titleEl.textContent : "Events") + "</title>\n" +
      "<style>\n" + exportStylesheet(template) + "\n</style>\n" +
      '<div style="' +
      (EXPORT_WRAPPERS[template] || EXPORT_WRAPPERS[DEFAULT_TEMPLATE]) + '">\n' +
      readable(inlineStyles(body, template)) + "\n</div>\n";
  }

  /* ----------------------------------------------------------------- the page ----- */

  function ready(fn) {
    if (document.readyState !== "loading") fn();
    else document.addEventListener("DOMContentLoaded", fn);
  }

  /* Guarded so the file can also be required by a Node harness, where there is no
     document to wire anything to. Without this the harness crashes on load and the
     syntax check would be the only thing the JS ever got. */
  if (typeof document !== "undefined") ready(start);

  function start() {
    var $ = function (id) { return document.getElementById(id); };

    var pubDate = $("pubdate"), pubTime = $("pubtime");
    var deadlineDate = $("deadlinedate"), deadlineTime = $("deadlinetime");
    var nowEl = $("now"), purposeEl = $("purpose"), styleEl = $("style");
    var sourcesEl = $("sources"), sourceCountEl = $("source-count");
    var statusEl = $("status"), resultsEl = $("results"), errorEl = $("error");
    var exportEl = $("export"), copyStateEl = $("copy-state");

    /** slug -> the feed's manifest record. Filled from status.json, never written here. */
    var feeds = {};
    /** The declared publications, from status.json. The page holds no schedule of its own. */
    var publications = {};
    /** The declared export layouts, likewise: name -> label, note, default. */
    var layouts = {};
    /** slug -> array of events, cached so toggling a checkbox off and on is free. */
    var loaded = {};
    /** Event ids the editor has unticked. Ids, not indices: the set survives a change of
        edition, and an event that comes back is still the one that was set aside. */
    var dropped = {};
    /** What the last render included, so the calendar reminder can say how many. */
    var lastIncludedCount = 0;
    /** The edition the table on screen belongs to, for handlers that fire after it. */
    var lastEdition = null;
    /** The partition the table on screen came from, for the same reason. */
    var lastResult = { included: [] };
    /* No second copy of the listing is kept. An earlier version held the built tree in a
       variable and then handed its children to the page with `replaceChildren`, which
       *moves* nodes rather than copying them -- so the variable was left an empty div and
       both export buttons silently produced nothing. The element on the page is the one
       source of truth: what is shown is what is exported, and they cannot diverge. */

    function setStatus(text, kind) {
      statusEl.textContent = text || "";
      statusEl.className = "status" + (kind ? " " + kind : "");
      statusEl.hidden = !text;
    }

    /** The selected publication's schedule, or the default when none is chosen. */
    function selectedSchedule() {
      var chosen = publications[purposeEl.value];
      return (chosen && chosen.schedule) || null;
    }

    /**
     * The layout a publication declares, which is what the switcher is *preset* to.
     *
     * Preset rather than locked. An editor composing the DaIS edition may still want the
     * engineering shape to paste somewhere else, and a control that silently reverts on
     * the next redraw is worse than no control.
     */
    function declaredTemplate() {
      var chosen = publications[purposeEl.value];
      return (chosen && chosen.template) || DEFAULT_TEMPLATE;
    }

    /** The layout actually in force: whatever the switcher says. */
    function selectedTemplate() {
      return styleEl.value || declaredTemplate();
    }

    /** The layout the manifest marks `default: true`, or the file's own fallback. */
    function defaultLayout() {
      var marked = Object.keys(layouts).filter(function (name) {
        return layouts[name] && layouts[name]["default"];
      });
      return marked.length === 1 ? marked[0] : DEFAULT_TEMPLATE;
    }

    /* The switcher's own note, so an editor knows what they are about to get without
       pasting it somewhere to find out. Also says when the style is not the one the
       selected publication declares, since that is a state somebody can end up in by
       accident and then not notice in the preview. */
    function describeStyle() {
      var chosen = layouts[styleEl.value] || {};
      var note = chosen.note || "";
      if (purposeEl.value && styleEl.value !== declaredTemplate()) {
        var declared = layouts[declaredTemplate()] || {};
        note += (note ? " " : "") + "Not the layout " +
          ((publications[purposeEl.value] || {}).label || purposeEl.value) +
          " declares, which is " + (declared.label || declaredTemplate()) + ".";
      }
      $("style-note").textContent = note;
    }

    function selectedSlugs() {
      return Array.prototype.slice
        .call(sourcesEl.querySelectorAll("input[type=checkbox]:checked"))
        .map(function (box) { return box.value; });
    }

    /* ---------------------------------------------------------- discovery ------- */

    /**
     * When the feeds were last gathered, and whether anything is wrong.
     *
     * `generatedAt` is when the Publish workflow last ran; a feed's own `lastSuccessAt`
     * is when its content last came off a live fetch. Those differ whenever a source was
     * inside its cadence and deliberately not refetched, so both are reported -- saying
     * only the first would imply every feed had just been read.
     */
    function renderFeedHealth(manifest) {
      var host = $("feed-health");
      host.replaceChildren();

      var summary = manifest.summary || {};
      var when = manifest.generatedAt;
      /* `generatedAt` ends in Z, so it is an unambiguous instant and `Date` reads it
         correctly -- unlike the feeds' own wall-clock times, which must never go near a
         Date because that would apply the reader's timezone. */
      var at = when ? new Date(when) : null;
      var minutes = at && !isNaN(at.getTime())
        ? Math.round((Date.now() - at.getTime()) / 60000)
        : null;

      var say = function (text, kind) {
        var span = document.createElement("span");
        if (kind) span.className = kind;
        span.textContent = text;
        host.appendChild(span);
      };
      var dot = function () { host.appendChild(document.createTextNode("  ·  ")); };

      say("Checked " + (minutes === null ? when
        : minutes < 1 ? "less than a minute ago"
        : minutes === 1 ? "1 minute ago"
        : minutes < 90 ? minutes + " minutes ago"
        : Math.round(minutes / 60) + " hours ago") + " by the Publish workflow");

      dot();
      say((summary.ok || 0) + " feeds ok");
      if (summary.empty) { dot(); say(summary.empty + " empty"); }

      if (summary.stale) {
        dot();
        say(summary.stale + " stale", "warn");
      }
      if (summary.failed) {
        dot();
        say(summary.failed + " failed", "bad");
      }
      if (!summary.stale && !summary.failed) {
        dot();
        say("none stale or failed", "ok");
      }

      // A feed inside its cadence was not refetched this run, which is by design and
      // worth saying rather than letting the run time imply otherwise.
      var stamps = (manifest.feeds || [])
        .filter(function (f) {
          return f.path.indexOf("feeds/") === 0 && f.status !== "disabled" && f.lastSuccessAt;
        })
        .map(function (f) { return f.lastSuccessAt; })
        .sort();
      if (stamps.length && stamps[0] !== when) {
        dot();
        say("oldest feed content " + prettyStamp(stamps[0]));
      }
    }

    function buildSourceList(manifest) {
      var records = (manifest.feeds || []).filter(function (f) {
        return typeof f.path === "string" && f.path.indexOf("feeds/") === 0;
      });

      records.sort(function (a, b) {
        return (a.label || a.path) < (b.label || b.path) ? -1 : 1;
      });

      publications = manifest.purposes || {};
      layouts = manifest.templates || {};
      var purposes = {};
      records.forEach(function (record) {
        var slug = record.path.split("/")[1];
        feeds[slug] = record;
        (record.purposes || []).forEach(function (p) { purposes[p] = true; });

        var live = record.status !== "disabled";
        var li = document.createElement("li");
        if (!live) li.className = "off";

        var label = document.createElement("label");
        var box = document.createElement("input");
        box.type = "checkbox";
        box.value = slug;
        box.checked = live;
        box.disabled = !live;
        label.appendChild(box);

        var name = document.createElement("span");
        name.className = "name";
        name.textContent = record.label || slug;
        label.appendChild(name);

        var count = document.createElement("span");
        count.className = "count";
        /* A source we cannot read says why, rather than showing a zero that would be
           indistinguishable from a unit with nothing on. */
        count.textContent = live ? String(record.events) : "unavailable";
        if (!live && record.detail) label.title = record.detail;
        label.appendChild(count);

        li.appendChild(label);
        sourcesEl.appendChild(li);
      });

      Object.keys(purposes).sort().forEach(function (name) {
        var option = document.createElement("option");
        option.value = name;
        // The publication's own label where the manifest declares one; a purpose a feed
        // names but nothing declares still appears, under its bare name.
        option.textContent = (publications[name] && publications[name].label) || name;
        purposeEl.appendChild(option);
      });

      /* The export's style switcher, from the manifest for the same reason as everything
         else on this page: the layouts are a declared vocabulary, and a list written here
         would be a second copy to keep in step with `config/sources.yaml`. */
      Object.keys(layouts).sort().forEach(function (name) {
        var option = document.createElement("option");
        option.value = name;
        option.textContent = layouts[name].label || name;
        if (layouts[name].note) option.title = layouts[name].note;
        styleEl.appendChild(option);
      });
      styleEl.value = defaultLayout();
      describeStyle();

      sourcesEl.addEventListener("change", function () { refresh(); });
    }

    function loadSelected() {
      var wanted = selectedSlugs().filter(function (slug) { return !loaded[slug]; });
      return Promise.all(wanted.map(function (slug) {
        return fetch("feeds/" + slug + "/events.json", { cache: "no-store" })
          .then(function (r) {
            if (!r.ok) throw new Error(slug + ": HTTP " + r.status);
            return r.json();
          })
          .then(function (events) {
            loaded[slug] = Array.isArray(events) ? events : [];
          })
          .catch(function (err) {
            loaded[slug] = [];
            return err.message;
          });
      })).then(function (problems) {
        return problems.filter(function (p) { return typeof p === "string"; });
      });
    }

    /* ------------------------------------------------------------- rendering ---- */

    /**
     * The two facts the controls imply, as labelled values rather than a sentence.
     *
     * They were one run-on line ending "12:00 p.m.." -- a full stop after an abbreviation
     * that already ends in one. Two labelled facts are also easier to check at a glance,
     * which is what someone setting a date is actually doing.
     */
    function renderDerived(edition, hours) {
      var derived = $("derived");
      derived.replaceChildren();
      if (!edition) {
        $("pubday").innerHTML = "&nbsp;";
        $("deadlineday").innerHTML = "&nbsp;";
        return;
      }
      $("pubday").textContent = weekdayName(edition.publicationDate) +
        (edition.shifted ? " — shifted from its usual weekday" : "");
      $("deadlineday").innerHTML = edition.deadlineAt
        ? weekdayName(edition.deadlineAt.slice(0, 10))
        : "&nbsp;";

      var fact = function (label, value, extra) {
        var wrap = document.createElement("div");
        var dt = document.createElement("dt");
        dt.textContent = label;
        var dd = document.createElement("dd");
        dd.textContent = value;
        if (extra) {
          var note = document.createElement("span");
          note.className = "aside";
          note.textContent = extra;
          dd.appendChild(note);
        }
        wrap.appendChild(dt);
        wrap.appendChild(dd);
        derived.appendChild(wrap);
        return dd;
      };

      fact(
        "Covers",
        prettyDate(edition.coverageStart.slice(0, 10)) + " – " +
          prettyDate(edition.coverageEnd.slice(0, 10))
      );
      /* No deadline, no row. DaIS's edition states none -- it says only to send an
         email -- and a "Submissions close" line with nothing after it reads like a value
         that failed to load rather than a publication that does not have one. */
      if (!edition.deadlineAt) {
        fact("Submissions", "No deadline stated for this publication");
        return;
      }
      var close = fact(
        "Submissions close",
        "",
        hours === null ? "" : "  " + relativeHours(hours)
      );
      close.insertBefore(deadlineControl(edition), close.firstChild);
    }

    /**
     * The deadline, as a button offering to put it in a calendar.
     *
     * The date is the one thing on this page somebody wants to keep, and retyping it into
     * a calendar is exactly the sort of transcription this project exists to remove.
     */
    function deadlineControl(edition) {
      var wrap = document.createElement("span");
      wrap.className = "deadline";

      var toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "linkbtn deadline-toggle";
      toggle.textContent = prettyStamp(edition.deadlineAt);
      toggle.setAttribute("aria-expanded", "false");
      toggle.title = "Add this deadline to a calendar";

      var menu = document.createElement("span");
      menu.className = "deadline-menu";
      menu.hidden = true;

      var counts = lastIncludedCount;
      var here = window.location.href;

      var google = document.createElement("a");
      google.className = "deadline-choice";
      google.setAttribute("href", googleCalendarUrl(edition, counts, here));
      google.setAttribute("target", "_blank");
      google.setAttribute("rel", "noopener");
      google.textContent = "Google Calendar";
      menu.appendChild(google);

      var ics = document.createElement("button");
      ics.type = "button";
      ics.className = "linkbtn deadline-choice";
      ics.textContent = "Download .ics";
      ics.addEventListener("click", function () {
        download(
          deadlineIcs(edition, counts, here),
          "text/calendar;charset=utf-8",
          "newsletter-deadline-" + edition.id + ".ics"
        );
        close();
      });
      menu.appendChild(ics);

      function close() {
        menu.hidden = true;
        toggle.setAttribute("aria-expanded", "false");
      }
      toggle.addEventListener("click", function (event) {
        event.stopPropagation();
        menu.hidden = !menu.hidden;
        toggle.setAttribute("aria-expanded", menu.hidden ? "false" : "true");
      });
      // Dismiss on a click elsewhere or on Escape, so the menu cannot be left stranded
      // open over the rest of the page.
      document.addEventListener("click", close);
      wrap.addEventListener("click", function (e) { e.stopPropagation(); });
      document.addEventListener("keydown", function (e) {
        if (e.key === "Escape") close();
      });

      wrap.appendChild(toggle);
      wrap.appendChild(menu);
      return wrap;
    }

    /** "in 5 hours" / "3 days ago", from the "viewing as of" control. */
    function relativeHours(hours) {
      var ahead = hours >= 0;
      var size = Math.abs(hours);
      var amount = size < 48
        ? Math.round(size) + (Math.round(size) === 1 ? " hour" : " hours")
        : Math.round(size / 24) + " days";
      return ahead ? "in " + amount : amount + " ago";
    }

    function entry(list, term, value) {
      var wrap = document.createElement("div");
      var dt = document.createElement("dt");
      dt.textContent = term;
      var dd = document.createElement("dd");
      dd.textContent = value;
      wrap.appendChild(dt);
      wrap.appendChild(dd);
      list.appendChild(wrap);
    }

    function renderSummary(edition, result, phase) {
      var summary = $("summary");
      summary.replaceChildren();
      entry(summary, "Events the editors receive", String(result.included.length));
      entry(summary, "Awaiting a real title", String(result.placeholders));
      entry(summary, "Sources selected", String(selectedSlugs().length));
      if (phase) entry(summary, "Status", phase);

      var details = $("details-list");
      details.replaceChildren();
      entry(details, "Edition", edition.id + " (week of " + prettyDate(edition.weekStart) + ")");
      entry(details, "Publishes", prettyStamp(edition.publicationAt));
      entry(details, "Deadline",
            edition.deadlineAt ? prettyStamp(edition.deadlineAt) : "none stated");
      entry(details, "Coverage window", edition.coverageStart + " to " + edition.coverageEnd);
    }

    /** An event's title, linked to its page when the feed carries one. */
    function eventLink(item) {
      if (!item.urlRef) {
        return document.createTextNode(item.title || "(no title)");
      }
      var link = document.createElement("a");
      link.href = item.urlRef;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = item.title || "(no title)";
      return link;
    }

    /** The sponsors, each linked to its own unit, as the export does. */
    function sponsorCell(item) {
      var td = document.createElement("td");
      item.sponsors.forEach(function (sponsor, index) {
        if (index) td.appendChild(document.createTextNode(", "));
        if (!sponsor.home) {
          td.appendChild(document.createTextNode(sponsor.label));
          return;
        }
        var link = document.createElement("a");
        link.href = sponsor.home;
        link.target = "_blank";
        link.rel = "noopener";
        link.textContent = sponsor.label;
        td.appendChild(link);
      });
      return td;
    }

    function cell(row, text, className) {
      var td = document.createElement("td");
      td.textContent = text;
      if (className) td.className = className;
      row.appendChild(td);
      return td;
    }

    function renderRows(result) {
      var body = $("rows");
      body.replaceChildren();
      $("caption").textContent = result.included.length
        ? result.included.length + " event(s), earliest first"
        : "No events fall in this edition.";

      result.included.forEach(function (item) {
        var row = document.createElement("tr");

        var picker = document.createElement("td");
        picker.className = "pick";
        var box = document.createElement("input");
        box.type = "checkbox";
        box.checked = !dropped[item.raw.id];
        box.setAttribute("aria-label", "Include " + (item.title || "this event") +
                                       " in the export");
        box.addEventListener("change", function () {
          if (box.checked) delete dropped[item.raw.id];
          else dropped[item.raw.id] = true;
          // Only the export changes. The table is what the editorial system receives,
          // and unticking a row does not alter that.
          renderExport(lastEdition, result);
          syncPickAll(result);
          syncQueryState();
        });
        picker.appendChild(box);
        row.appendChild(picker);

        var grade = readiness(item);
        var stateCell = document.createElement("td");
        var pill = document.createElement("span");
        pill.className = "pill " +
          (grade.state === "ready" ? "ok" : grade.state === "fix" ? "warn" : "bad");
        pill.textContent = grade.label;
        // The detail lives in a tooltip rather than the cell: six columns of prose would
        // bury the one thing this column exists to make scannable.
        pill.title = grade.reasons.concat(grade.gaps).join("; ") || "nothing missing";
        stateCell.appendChild(pill);
        row.appendChild(stateCell);
        row.classList.add("state-" + grade.state);
        if (dropped[item.raw.id]) row.classList.add("dropped");

        cell(row, prettyStamp(item.startTime), "when");
        var title = document.createElement("td");
        title.appendChild(eventLink(item));
        if (item.placeholder) {
          var flag = document.createElement("span");
          flag.className = "placeholder-flag";
          flag.textContent = " — no title yet";
          title.appendChild(flag);
        }
        row.appendChild(title);
        cell(row, item.series);
        cell(row, item.speakers.join("; "));
        cell(row, item.location);
        row.appendChild(sponsorCell(item));
        body.appendChild(row);
      });
      syncPickAll(result);
    }

    /** Keep the header box showing the state of the rows under it. */
    function syncPickAll(result) {
      var all = $("pick-all");
      var kept = keptOf(result).length;
      all.checked = kept === result.included.length && kept > 0;
      all.indeterminate = kept > 0 && kept < result.included.length;
    }

    function keptOf(result) {
      return result.included.filter(function (item) { return !dropped[item.raw.id]; });
    }

    function renderExcluded(result) {
      var body = $("excluded-rows");
      body.replaceChildren();
      var rows = result.excluded.concat(result.malformed);
      $("excluded-wrap").hidden = rows.length === 0;
      $("excluded-count").textContent =
        rows.length + " event" + (rows.length === 1 ? "" : "s") +
        " in the selected feeds but outside this edition";

      rows.forEach(function (item) {
        var row = document.createElement("tr");
        cell(row, item.malformedStart ? (item.startTime || "(missing)") : prettyStamp(item.startTime), "when");
        var exTitle = document.createElement("td");
        exTitle.appendChild(eventLink(item));
        row.appendChild(exTitle);
        cell(row, item.series);
        row.appendChild(sponsorCell(item));
        cell(row, item.malformedStart ? "Unreadable start time" : item.reason, "reason");
        body.appendChild(row);
      });
    }

    /**
     * The repeated-talk note, openable to show which events it means.
     *
     * A count on its own tells an editor a duplicate exists and not where to look, which
     * leaves them scanning the table for it -- the work the note was meant to save.
     */
    function renderCollisions(result) {
      var host = $("collisions");
      host.replaceChildren();
      var groups = result.collisionGroups || [];
      host.hidden = groups.length === 0;
      if (!groups.length) return;

      var repeats = result.collisions;
      var details = document.createElement("details");
      details.className = "collisions";

      var summary = document.createElement("summary");
      summary.textContent =
        repeats + " event" + (repeats === 1 ? "" : "s") + " look like the same talk listed " +
        "by more than one selected source; the feeds do not de-duplicate, so each appears";
      details.appendChild(summary);

      groups.forEach(function (group) {
        var wrap = document.createElement("div");
        wrap.className = "collision";

        var when = document.createElement("p");
        when.className = "collision-when";
        when.textContent = prettyStamp(group[0].startTime);
        wrap.appendChild(when);

        var list = document.createElement("ul");
        group.forEach(function (item) {
          var li = document.createElement("li");
          li.appendChild(eventLink(item));
          var who = document.createElement("span");
          who.className = "reason";
          // Named rather than slugged, and the *reason* they matched is spelled out: for
          // a synthesized title that is the speaker, not the title, and saying so stops
          // the match looking arbitrary.
          who.textContent = " — " +
            item.sponsors.map(function (x) { return x.label; }).join(", ") +
            (item.placeholder && item.names.length
              ? "; matched on the speaker, since neither title is announced yet"
              : "");
          li.appendChild(who);
          list.appendChild(li);
        });
        wrap.appendChild(list);
        details.appendChild(wrap);
      });
      host.appendChild(details);
    }

    function renderExport(edition, result) {
      var kept = keptOf(result);
      var template = selectedTemplate();
      var listing = buildListing(edition, kept, document, template);
      exportEl.replaceChildren.apply(exportEl, Array.prototype.slice.call(listing.childNodes));
      /* The preview carries the chosen style too. A preview in one shape and an export in
         another is the mismatch that let an empty download ship: what is on screen has to
         be what leaves. */
      Object.keys(EXPORT_STYLES).forEach(function (name) {
        exportEl.classList.toggle("style-" + name, name === template);
      });
      copyStateEl.textContent = "";
      $("export-count").textContent = kept.length === result.included.length
        ? kept.length + " event(s), all of them"
        : kept.length + " of " + result.included.length + " event(s); " +
          (result.included.length - kept.length) + " left out above";
      // Marked on the rows too, so the two views cannot disagree about what is in.
      // `classList` rather than `className`: assigning would wipe the readiness class.
      Array.prototype.forEach.call($("rows").children, function (row, index) {
        var item = result.included[index] || { raw: {} };
        row.classList.toggle("dropped", Boolean(dropped[item.raw.id]));
      });
    }

    /** Hand the browser a file. One implementation, two callers. */
    function download(body, type, name) {
      var url = URL.createObjectURL(new Blob([body], { type: type }));
      var link = document.createElement("a");
      link.href = url;
      link.download = name;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    }

    function syncQueryState() {
      if (!window.history || !window.history.replaceState) return;
      var params = new URLSearchParams();
      params.set("pub", pubDate.value);
      if (pubTime.value !== "12:00") params.set("pubtime", pubTime.value);
      /* A publication with no deadline has no deadline override to carry, and writing an
         empty one into the link would come back as the other publication's default. */
      if (deadlineDeclared()) {
        if (deadlineDate.value !== defaultDeadlineDate(pubDate.value)) {
          params.set("deadline", deadlineDate.value);
        }
        if (deadlineTime.value !== "12:00") params.set("deadlinetime", deadlineTime.value);
      }
      if (nowEl.value) params.set("now", nowEl.value);
      if (purposeEl.value) params.set("purpose", purposeEl.value);
      /* Only an override travels. A link carrying the layout its own purpose already
         declares would pin it, so a later change to that purpose's template would not
         reach anybody holding the link. */
      if (styleEl.value !== declaredTemplate()) params.set("style", styleEl.value);
      params.set("sources", selectedSlugs().join(","));
      var out = Object.keys(dropped);
      if (out.length) params.set("drop", out.join(","));
      window.history.replaceState(null, "", "?" + params.toString());
    }

    /* -------------------------------------------------------------- the loop ---- */

    function refresh() {
      var edition;
      try {
        edition = resolveEdition({
          schedule: selectedSchedule(),
          publicationDate: pubDate.value,
          publicationTime: pubTime.value,
          deadlineDate: deadlineDate.value,
          deadlineTime: deadlineTime.value
        });
      } catch (err) {
        resultsEl.hidden = true;
        renderDerived(null, null);
        setStatus(err.message, "error");
        return;
      }

      var slugs = selectedSlugs();
      sourceCountEl.textContent = slugs.length
        ? slugs.length + " selected"
        : "none selected";

      if (!slugs.length) {
        resultsEl.hidden = true;
        renderDerived(edition, null);
        setStatus("Select at least one source to build an edition.", "warn");
        syncQueryState();
        return;
      }

      setStatus("Loading the selected feeds…");
      loadSelected().then(function (problems) {
        var events = [];
        slugs.forEach(function (slug) {
          events = events.concat(loaded[slug] || []);
        });

        var result = partition(events, edition, purposeEl.value, feeds);
        var nowStamp = nowEl.value ? nowEl.value + ":00" : null;
        var phase = nowStamp ? phaseAt(nowStamp, edition) : null;
        var hours = nowStamp ? hoursBetween(nowStamp, edition.deadlineAt) : null;

        lastEdition = edition;
        lastResult = result;
        lastIncludedCount = keptOf(result).length;
        renderSummary(edition, result, phase);
        renderDerived(edition, hours);
        renderRows(result);
        renderExcluded(result);
        renderExport(edition, result);
        lastIncludedCount = keptOf(result).length;
        $("json").textContent = JSON.stringify(
          result.included.map(function (item) {
            return Object.assign({}, item.raw, { newsletterEdition: edition.id });
          }), null, 2
        );

        resultsEl.hidden = false;
        syncQueryState();

        var notes = problems.slice();
        if (result.malformed.length) {
          notes.push(result.malformed.length + " event(s) have an unreadable start time " +
                     "and were left out of both lists rather than guessed at");
        }
        setStatus(notes.length ? "Note: " + notes.join("; ") + "." : "",
                  notes.length ? "warn" : null);
        renderCollisions(result);
      });
    }

    /* --------------------------------------------------------------- wiring ----- */

    /** Does the selected publication state a submission deadline at all? */
    function deadlineDeclared() {
      var plan = selectedSchedule();
      return Boolean(!plan || plan.deadline);
    }

    /* Two empty date and time fields under "Advanced" invite somebody to fill them in,
       and nothing would happen if they did -- a control with no effect is worse than no
       control. They are hidden rather than disabled so the panel does not show a row of
       greyed-out fields for every publication that works this way. */
    function syncDeadlineFields() {
      var shown = deadlineDeclared();
      $("deadline-date-field").hidden = !shown;
      $("deadline-time-field").hidden = !shown;
    }

    function deriveDeadline() {
      syncDeadlineFields();
      if (!DATE_RE.test(pubDate.value)) return;
      if (!deadlineDeclared()) {
        // This publication states no deadline, so there is nothing to derive and the
        // Advanced field is left empty rather than filled with an invented date.
        deadlineDate.value = "";
        return;
      }
      deadlineDate.value = defaultDeadlineDate(pubDate.value);
    }

    function resetToNextEdition() {
      var now = new Date();
      var pad = function (n) { return String(n).padStart(2, "0"); };
      var stamp = now.getFullYear() + "-" + pad(now.getMonth() + 1) + "-" +
        pad(now.getDate()) + "T" + pad(now.getHours()) + ":" + pad(now.getMinutes()) + ":00";
      pubDate.value = nextEditionDate(stamp, selectedSchedule());
      pubTime.value = "12:00";
      deadlineTime.value = "12:00";
      deriveDeadline();
    }

    function applyQueryState() {
      var params = new URLSearchParams(window.location.search);
      if (!params.has("pub")) return false;
      pubDate.value = params.get("pub");
      if (params.has("pubtime")) pubTime.value = params.get("pubtime");
      if (params.has("deadlinetime")) deadlineTime.value = params.get("deadlinetime");
      if (params.has("now")) nowEl.value = params.get("now");
      if (params.has("purpose")) purposeEl.value = params.get("purpose");
      styleEl.value = params.get("style") || declaredTemplate();
      describeStyle();
      /* After the purpose, because whether there is a deadline at all depends on it. */
      deadlineDate.value = deadlineDeclared()
        ? params.get("deadline") || defaultDeadlineDate(pubDate.value)
        : "";
      syncDeadlineFields();
      if (params.has("drop")) {
        params.get("drop").split(",").filter(Boolean).forEach(function (id) {
          dropped[id] = true;
        });
      }
      if (params.has("sources")) {
        var wanted = params.get("sources").split(",").filter(Boolean);
        Array.prototype.forEach.call(sourcesEl.querySelectorAll("input"), function (box) {
          if (!box.disabled) box.checked = wanted.indexOf(box.value) !== -1;
        });
      }
      return true;
    }

    pubDate.addEventListener("change", function () { deriveDeadline(); refresh(); });
    [pubTime, deadlineDate, deadlineTime, nowEl].forEach(function (el) {
      el.addEventListener("change", refresh);
    });

    /* Choosing a publication changes its schedule, so the date it resets to changes with
       it. Re-deriving beats leaving a Monday date selected under a Thursday schedule. */
    purposeEl.addEventListener("change", function () {
      // The publication's own layout is what an editor almost always wants, so choosing
      // one presets the switcher. They can still change it afterwards; it is preset on
      // each purpose change rather than forced on every redraw.
      styleEl.value = declaredTemplate();
      describeStyle();
      resetToNextEdition();
      refresh();
    });

    styleEl.addEventListener("change", function () {
      describeStyle();
      if (lastEdition) renderExport(lastEdition, lastResult);
      syncQueryState();
    });

    $("reset").addEventListener("click", function () { resetToNextEdition(); refresh(); });

    $("pick-all").addEventListener("change", function () {
      var keep = $("pick-all").checked;
      Array.prototype.forEach.call($("rows").querySelectorAll("td.pick input"), function (box) {
        box.checked = keep;
      });
      dropped = {};
      if (!keep) {
        lastResult.included.forEach(function (item) { dropped[item.raw.id] = true; });
      }
      renderExport(lastEdition, lastResult);
      syncPickAll(lastResult);
      syncQueryState();
    });

    $("all-sources").addEventListener("click", function () {
      Array.prototype.forEach.call(sourcesEl.querySelectorAll("input"), function (box) {
        if (!box.disabled) box.checked = true;
      });
      refresh();
    });

    $("no-sources").addEventListener("click", function () {
      Array.prototype.forEach.call(sourcesEl.querySelectorAll("input"), function (box) {
        box.checked = false;
      });
      refresh();
    });

    /* Purpose and source selection stay independent -- neither silently overrides the
       other -- but the common intent is one click. */
    $("pick-purpose").addEventListener("click", function () {
      var purpose = purposeEl.value;
      Array.prototype.forEach.call(sourcesEl.querySelectorAll("input"), function (box) {
        if (box.disabled) return;
        var record = feeds[box.value] || {};
        box.checked = !purpose || (record.purposes || []).indexOf(purpose) !== -1;
      });
      refresh();
    });

    $("copy").addEventListener("click", function () {
      var html = inlineStyles(exportEl.innerHTML, selectedTemplate());
      var text = exportEl.innerText || exportEl.textContent || "";
      if (!html) return;
      var done = function () { copyStateEl.textContent = "Copied."; };
      var failed = function () {
        copyStateEl.textContent = "Could not copy — select the listing and copy it by hand.";
      };
      /* Rich text where the browser allows it, so a paste into Mailchimp keeps the day
         headings and bold titles; plain text is the fallback, not the goal. */
      if (window.ClipboardItem && navigator.clipboard && navigator.clipboard.write) {
        navigator.clipboard.write([new ClipboardItem({
          "text/html": new Blob([html], { type: "text/html" }),
          "text/plain": new Blob([text], { type: "text/plain" })
        })]).then(done, function () {
          navigator.clipboard.writeText(text).then(done, failed);
        });
      } else if (navigator.clipboard) {
        navigator.clipboard.writeText(text).then(done, failed);
      } else {
        failed();
      }
    });

    $("download").addEventListener("click", function () {
      var doc = exportDocument(exportEl, selectedTemplate());
      if (!doc) return;
      download(doc, "text/html;charset=utf-8",
               "events-newsletter-" + (pubDate.value || "edition") + ".html");
    });

    /* -------------------------------------------------------------- start up ---- */

    fetch("status.json", { cache: "no-store" })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (manifest) {
        renderFeedHealth(manifest);
        buildSourceList(manifest);
        resetToNextEdition();
        applyQueryState();
        refresh();
      })
      .catch(function () {
        setStatus("");
        errorEl.style.display = "block";
      });
  }

  /* Exported for a Node harness, which is how the date and edition rules are checked
     without a browser. Harmless in a page: nothing else reads it. */
  if (typeof module === "object" && module.exports) {
    module.exports = {
      addDays: addDays, weekStartFor: weekStartFor, weekdayName: weekdayName,
      defaultDeadlineDate: defaultDeadlineDate, resolveEdition: resolveEdition,
      nextEditionDate: nextEditionDate, anchorDate: anchorDate,
      weekdayOffset: weekdayOffset, DEFAULT_SCHEDULE: DEFAULT_SCHEDULE,
      phaseAt: phaseAt, inWindow: inWindow, prettyClock: prettyClock,
      prettyDate: prettyDate, locationText: locationText, speakerText: speakerText,
      speakerNames: speakerNames,
      collisionKey: collisionKey, groupByDay: groupByDay, plural: plural,
      readiness: readiness,
      buildListing: buildListing, decorate: decorate, exportDocument: exportDocument,
      buildInlineListing: buildInlineListing, shortDate: shortDate,
      timeRange: timeRange, whenAndWhere: whenAndWhere, bareClock: bareClock,
      plainLocation: plainLocation,
      firstParagraph: firstParagraph,
      inlineStyles: inlineStyles, exportStylesheet: exportStylesheet, readable: readable,
      zonedToUTC: zonedToUTC, utcStamp: utcStamp, deadlineEvent: deadlineEvent,
      deadlineIcs: deadlineIcs, googleCalendarUrl: googleCalendarUrl,
      icsText: icsText, foldLine: foldLine, sentence: sentence,
      EXPORT_STYLES: EXPORT_STYLES, stylesFor: stylesFor,
      EXPORT_WRAPPERS: EXPORT_WRAPPERS,
      DEFAULT_TEMPLATE: DEFAULT_TEMPLATE, spansWholeDays: spansWholeDays,
      partition: partition, hoursBetween: hoursBetween
    };
  }
})();
