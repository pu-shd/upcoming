/* A DOM small enough to read, faithful in the one respect that matters.
 *
 * `appendChild` and `replaceChildren` **move** a node: they detach it from whatever parent
 * it had first. That is the whole reason this file exists. The export buttons once read
 * from a variable holding a tree whose children had already been handed to the page, so
 * both of them silently produced an empty document -- and no test caught it, because the
 * listing builder was only ever exercised against a stub that could not reparent anything.
 *
 * Only what simulator.js actually touches is implemented. Anything else is deliberately
 * absent so a test cannot come to depend on behaviour this does not really model.
 */
"use strict";

function escapeText(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

/* Elements a browser serializes with no closing tag. The inline-date listing emits an
   `<hr>` between events, and a stub that wrote `</hr>` would have the export tests
   asserting markup no browser produces. */
const VOID_TAGS = new Set(["AREA", "BASE", "BR", "COL", "EMBED", "HR", "IMG", "INPUT",
                           "LINK", "META", "SOURCE", "TRACK", "WBR"]);

function makeElement(tag) {
  return {
    tagName: tag.toUpperCase(),
    className: "",
    textContent: "",
    childNodes: [],
    parentNode: null,
    attributes: {},

    setAttribute: function (name, value) {
      this.attributes[name] = String(value);
    },

    getAttribute: function (name) {
      return Object.prototype.hasOwnProperty.call(this.attributes, name)
        ? this.attributes[name]
        : null;
    },

    appendChild: function (child) {
      // A node has one parent. Attaching it somewhere else detaches it from here first.
      if (child.parentNode) child.parentNode.removeChild(child);
      child.parentNode = this;
      this.childNodes.push(child);
      return child;
    },

    removeChild: function (child) {
      var at = this.childNodes.indexOf(child);
      if (at !== -1) this.childNodes.splice(at, 1);
      child.parentNode = null;
      return child;
    },

    replaceChildren: function () {
      this.childNodes.slice().forEach(function (child) {
        this.removeChild(child);
      }, this);
      Array.prototype.forEach.call(arguments, function (child) {
        this.appendChild(child);
      }, this);
    },

    querySelector: function (selector) {
      var wanted = selector.toUpperCase();
      var found = null;
      var walk = function (node) {
        node.childNodes.forEach(function (child) {
          if (!found && child.tagName === wanted) found = child;
          if (!found) walk(child);
        });
      };
      walk(this);
      return found;
    },

    get innerHTML() {
      var own = this.textContent ? escapeText(this.textContent) : "";
      var inner = this.childNodes.map(function (c) { return c.outerHTML; }).join("");
      return own + inner;
    },

    get outerHTML() {
      // class first, then anything set explicitly -- the order a real DOM serializes them
      // in, since both follow attribute insertion order and className is assigned first.
      var attrs = this.className ? ' class="' + this.className + '"' : "";
      Object.keys(this.attributes).forEach(function (name) {
        attrs += " " + name + '="' + escapeText(this.attributes[name]).replace(/"/g, "&quot;") + '"';
      }, this);
      var tag = this.tagName.toLowerCase();
      if (VOID_TAGS.has(this.tagName)) return "<" + tag + attrs + ">";
      return "<" + tag + attrs + ">" + this.innerHTML + "</" + tag + ">";
    },

    get innerText() {
      var own = this.textContent || "";
      return own + this.childNodes.map(function (c) { return c.innerText; }).join("");
    }
  };
}

/* A text node: no tag, no attributes, just escaped content. The inline-date template
   needs them for the prose it builds around its links ("Hosted by " + a link). */
function makeText(value) {
  return {
    tagName: "",
    className: "",
    textContent: String(value),
    childNodes: [],
    parentNode: null,
    attributes: {},
    get innerHTML() { return escapeText(this.textContent); },
    get outerHTML() { return escapeText(this.textContent); },
    get innerText() { return this.textContent; },
    getAttribute: function () { return null; }
  };
}

module.exports = {
  document: { createElement: makeElement, createTextNode: makeText },
  makeElement: makeElement,
  makeText: makeText
};
