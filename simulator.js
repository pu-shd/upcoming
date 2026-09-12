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
    var publicationDate = input.publicationDate;
    var weekStart = weekStartFor(publicationDate);
    var deadlineDate = input.deadlineDate || defaultDeadlineDate(publicationDate);
    if (!DATE_RE.test(deadlineDate)) throw new Error("The deadline date is not a date.");

    return {
      id: weekStart,
      weekStart: weekStart,
      publicationDate: publicationDate,
      publicationAt: publicationDate + "T" + normalizeTime(input.publicationTime, "12:00:00"),
      deadlineAt: deadlineDate + "T" + normalizeTime(input.deadlineTime, "12:00:00"),
      coverageStart: publicationDate + "T00:00:00",
      coverageEnd: addDays(weekStart, 6) + "T23:59:59",
      shifted: publicationDate !== weekStart
    };
  }

  function phaseAt(nowStamp, edition) {
    if (!STAMP_RE.test(nowStamp || "")) return null;
    if (nowStamp >= edition.publicationAt) return "published";
    if (nowStamp >= edition.deadlineAt) return "closed";
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

  function decorate(event, feeds) {
    var sources = Array.isArray(event.sources) ? event.sources : [];
    return {
      raw: event,
      guid: event.guid || "",
      startTime: event.startTime || "",
      title: (event.title || "").trim(),
      /* A comma-joined multi-value series arrives as "A,B" because that is how the feed
         publishes it. Spacing it is presentation for a human composing a listing, not a
         change to what the feed says. */
      series: (event.series || "").trim().replace(/,(?=\S)/g, ", "),
      speakers: speakerText(event),
      /* Bare names, kept alongside the display strings. The two feeds listing the same
         talk agreed on "Rafael Gomez-Bombarelli" and disagreed on whether to include his
         institution, so a duplicate check on the display string misses it. */
      names: speakerNames(event),
      location: locationText(event),
      urlRef: event.urlRef || "",
      sources: sources,
      sponsors: sources.map(function (slug) {
        return (feeds[slug] && feeds[slug].label) || slug;
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

    var seen = {};
    var collisions = 0;
    included.forEach(function (item) {
      var key = collisionKey(item);
      if (seen[key]) collisions += 1;
      seen[key] = true;
    });

    return {
      included: included,
      excluded: excluded,
      malformed: malformed,
      collisions: collisions,
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

  /**
   * The listing as an element tree, used for the preview and as the copied markup.
   *
   * `doc` is a parameter rather than the global `document` so this can be rendered under a
   * stub and asserted -- the export is the part an editor actually pastes into Mailchimp,
   * so it is the last thing that should go unchecked.
   */
  function buildListing(edition, items, doc) {
    var root = doc.createElement("div");
    var heading = doc.createElement("h2");
    heading.textContent = "Events Newsletter: " +
      prettyDate(edition.coverageStart.slice(0, 10)).replace(/^\w+, /, "") + " - " +
      prettyDate(edition.coverageEnd.slice(0, 10)).replace(/^\w+, /, "") + ", " +
      edition.coverageEnd.slice(0, 4);
    root.appendChild(heading);

    if (!items.length) {
      var empty = doc.createElement("p");
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
        title.textContent = item.title || "(no title)";
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
        var add = function (label, value) {
          if (!value) return;
          var dt = doc.createElement("dt");
          dt.textContent = label;
          var dd = doc.createElement("dd");
          dd.textContent = value;
          dl.appendChild(dt);
          dl.appendChild(dd);
        };
        add(plural("Speaker", item.speakers), item.speakers.join("; "));
        add(plural("Sponsor", item.sponsors), item.sponsors.join("; "));
        add("Series:", item.series);
        add("Location:", item.location);
        block.appendChild(dl);

        root.appendChild(block);
      });
    });
    return root;
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
  var EXPORT_STYLES = {
    "h2": "font: 700 20px/1.3 Georgia, 'Times New Roman', serif; margin: 0 0 20px;",
    "h3.day": "font: 700 15px/1.3 Helvetica, Arial, sans-serif; margin: 28px 0 10px; "
      + "padding-bottom: 4px; border-bottom: 1px solid #d8d8d8;",
    "div.ev": "margin: 0 0 20px;",
    "p.ev-title": "font: 700 15px/1.4 Helvetica, Arial, sans-serif; margin: 0 0 2px;",
    "p.ev-time": "font: 400 14px/1.4 Helvetica, Arial, sans-serif; margin: 0 0 6px; "
      + "color: #555555;",
    "span.placeholder-flag": "font: 400 13px/1.4 Helvetica, Arial, sans-serif; "
      + "color: #9a6700;",
    "dl": "margin: 0; font: 400 14px/1.5 Helvetica, Arial, sans-serif;",
    "dt": "font-weight: 700; margin: 0;",
    "dd": "margin: 0 0 4px;"
  };

  /** The same rules as a stylesheet, for the file when it is simply opened in a browser. */
  function exportStylesheet() {
    return Object.keys(EXPORT_STYLES).map(function (selector) {
      return selector + " { " + EXPORT_STYLES[selector] + " }";
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
      .replace(/<div class="ev"/g, "\n<div class=\"ev\"")
      .replace(/<\/div>/g, "</div>\n")
      .trim();
  }

  function inlineStyles(html) {
    return Object.keys(EXPORT_STYLES).reduce(function (text, selector) {
      var parts = selector.split(".");
      var tag = parts[0];
      var cls = parts[1];
      var pattern = cls
        ? new RegExp("<" + tag + ' class="' + cls + '">', "g")
        : new RegExp("<" + tag + ">", "g");
      var open = cls
        ? "<" + tag + ' class="' + cls + '" style="' + EXPORT_STYLES[selector] + '">'
        : "<" + tag + ' style="' + EXPORT_STYLES[selector] + '">';
      return text.replace(pattern, open);
    }, html);
  }

  /**
   * The standalone document the download produces, built from the element on the page.
   *
   * A named function rather than a few lines inside a click handler, because a click
   * handler is the one place nothing can reach to test it -- and the first version of this
   * shipped broken, emitting a title and an empty body, precisely because it lived there.
   */
  function exportDocument(el) {
    var body = el ? el.innerHTML : "";
    if (!body) return "";
    var titleEl = el.querySelector("h2");
    return "<!doctype html>\n<meta charset=\"utf-8\">\n<title>" +
      (titleEl ? titleEl.textContent : "Events") + "</title>\n" +
      "<style>\n" + exportStylesheet() + "\n</style>\n" +
      '<div style="max-width: 640px; margin: 24px auto; padding: 0 16px; color: #17181c;">\n' +
      readable(inlineStyles(body)) + "\n</div>\n";
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
    var nowEl = $("now"), purposeEl = $("purpose");
    var sourcesEl = $("sources"), sourceCountEl = $("source-count");
    var statusEl = $("status"), resultsEl = $("results"), errorEl = $("error");
    var exportEl = $("export"), copyStateEl = $("copy-state");

    /** slug -> the feed's manifest record. Filled from status.json, never written here. */
    var feeds = {};
    /** slug -> array of events, cached so toggling a checkbox off and on is free. */
    var loaded = {};
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

    function selectedSlugs() {
      return Array.prototype.slice
        .call(sourcesEl.querySelectorAll("input[type=checkbox]:checked"))
        .map(function (box) { return box.value; });
    }

    /* ---------------------------------------------------------- discovery ------- */

    function buildSourceList(manifest) {
      var records = (manifest.feeds || []).filter(function (f) {
        return typeof f.path === "string" && f.path.indexOf("feeds/") === 0;
      });

      records.sort(function (a, b) {
        return (a.label || a.path) < (b.label || b.path) ? -1 : 1;
      });

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
        option.textContent = name;
        purposeEl.appendChild(option);
      });

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

    function renderDerived(edition, hours) {
      if (!edition) {
        $("derived").textContent = "";
        $("pubday").innerHTML = "&nbsp;";
        $("deadlineday").innerHTML = "&nbsp;";
        return;
      }
      $("pubday").textContent = weekdayName(edition.publicationDate) +
        (edition.shifted ? " — shifted from this week's Monday" : "");
      $("deadlineday").textContent = weekdayName(edition.deadlineAt.slice(0, 10));
      $("derived").textContent =
        "Covers " + prettyDate(edition.coverageStart.slice(0, 10)) + " through " +
        prettyDate(edition.coverageEnd.slice(0, 10)) + ". Submissions close " +
        prettyStamp(edition.deadlineAt) +
        (hours === null ? "." : ", " + Math.round(hours) + " hours from the time you are viewing as.");
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
      entry(details, "Deadline", prettyStamp(edition.deadlineAt));
      entry(details, "Coverage window", edition.coverageStart + " to " + edition.coverageEnd);
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
        cell(row, prettyStamp(item.startTime), "when");
        var title = cell(row, item.title || "(no title)");
        if (item.placeholder) {
          var flag = document.createElement("span");
          flag.className = "placeholder-flag";
          flag.textContent = " — no title yet";
          title.appendChild(flag);
        }
        cell(row, item.series);
        cell(row, item.speakers.join("; "));
        cell(row, item.location);
        cell(row, item.sources.join(", "), "mono");
        body.appendChild(row);
      });
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
        cell(row, item.title || "(no title)");
        cell(row, item.series);
        cell(row, item.sources.join(", "), "mono");
        cell(row, item.malformedStart ? "Unreadable start time" : item.reason, "reason");
        body.appendChild(row);
      });
    }

    function renderExport(edition, result) {
      var listing = buildListing(edition, result.included, document);
      exportEl.replaceChildren.apply(exportEl, Array.prototype.slice.call(listing.childNodes));
      copyStateEl.textContent = "";
    }

    function syncQueryState() {
      if (!window.history || !window.history.replaceState) return;
      var params = new URLSearchParams();
      params.set("pub", pubDate.value);
      if (pubTime.value !== "12:00") params.set("pubtime", pubTime.value);
      if (deadlineDate.value !== defaultDeadlineDate(pubDate.value)) {
        params.set("deadline", deadlineDate.value);
      }
      if (deadlineTime.value !== "12:00") params.set("deadlinetime", deadlineTime.value);
      if (nowEl.value) params.set("now", nowEl.value);
      if (purposeEl.value) params.set("purpose", purposeEl.value);
      params.set("sources", selectedSlugs().join(","));
      window.history.replaceState(null, "", "?" + params.toString());
    }

    /* -------------------------------------------------------------- the loop ---- */

    function refresh() {
      var edition;
      try {
        edition = resolveEdition({
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

        renderDerived(edition, hours);
        renderSummary(edition, result, phase);
        renderRows(result);
        renderExcluded(result);
        renderExport(edition, result);
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
        if (result.collisions) {
          notes.push(result.collisions + " event(s) look like the same talk listed by more " +
                     "than one selected source; the feeds do not de-duplicate, so both appear");
        }
        setStatus(notes.length ? "Note: " + notes.join("; ") + "." : "",
                  notes.length ? "warn" : null);
      });
    }

    /* --------------------------------------------------------------- wiring ----- */

    function deriveDeadline() {
      if (DATE_RE.test(pubDate.value)) {
        deadlineDate.value = defaultDeadlineDate(pubDate.value);
      }
    }

    function resetToNextEdition() {
      var today = new Date();
      var iso = today.getFullYear() + "-" +
        String(today.getMonth() + 1).padStart(2, "0") + "-" +
        String(today.getDate()).padStart(2, "0");
      pubDate.value = weekStartFor(iso);
      pubTime.value = "12:00";
      deadlineTime.value = "12:00";
      deriveDeadline();
    }

    function applyQueryState() {
      var params = new URLSearchParams(window.location.search);
      if (!params.has("pub")) return false;
      pubDate.value = params.get("pub");
      if (params.has("pubtime")) pubTime.value = params.get("pubtime");
      deadlineDate.value = params.get("deadline") || defaultDeadlineDate(pubDate.value);
      if (params.has("deadlinetime")) deadlineTime.value = params.get("deadlinetime");
      if (params.has("now")) nowEl.value = params.get("now");
      if (params.has("purpose")) purposeEl.value = params.get("purpose");
      if (params.has("sources")) {
        var wanted = params.get("sources").split(",").filter(Boolean);
        Array.prototype.forEach.call(sourcesEl.querySelectorAll("input"), function (box) {
          if (!box.disabled) box.checked = wanted.indexOf(box.value) !== -1;
        });
      }
      return true;
    }

    pubDate.addEventListener("change", function () { deriveDeadline(); refresh(); });
    [pubTime, deadlineDate, deadlineTime, nowEl, purposeEl].forEach(function (el) {
      el.addEventListener("change", refresh);
    });

    $("reset").addEventListener("click", function () { resetToNextEdition(); refresh(); });

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
      var html = inlineStyles(exportEl.innerHTML);
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
      var doc = exportDocument(exportEl);
      if (!doc) return;
      var url = URL.createObjectURL(new Blob([doc], { type: "text/html;charset=utf-8" }));
      var link = document.createElement("a");
      link.href = url;
      link.download = "events-newsletter-" + (pubDate.value || "edition") + ".html";
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    });

    /* -------------------------------------------------------------- start up ---- */

    fetch("status.json", { cache: "no-store" })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (manifest) {
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
      phaseAt: phaseAt, inWindow: inWindow, prettyClock: prettyClock,
      prettyDate: prettyDate, locationText: locationText, speakerText: speakerText,
      speakerNames: speakerNames,
      collisionKey: collisionKey, groupByDay: groupByDay, plural: plural,
      buildListing: buildListing, decorate: decorate, exportDocument: exportDocument,
      inlineStyles: inlineStyles, exportStylesheet: exportStylesheet, readable: readable,
      EXPORT_STYLES: EXPORT_STYLES,
      partition: partition, hoursBetween: hoursBetween
    };
  }
})();
