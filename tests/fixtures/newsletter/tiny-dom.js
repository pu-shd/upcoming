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

function makeElement(tag) {
  return {
    tagName: tag.toUpperCase(),
    className: "",
    textContent: "",
    childNodes: [],
    parentNode: null,

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
      var attrs = this.className ? ' class="' + this.className + '"' : "";
      var tag = this.tagName.toLowerCase();
      return "<" + tag + attrs + ">" + this.innerHTML + "</" + tag + ">";
    },

    get innerText() {
      var own = this.textContent || "";
      return own + this.childNodes.map(function (c) { return c.innerText; }).join("");
    }
  };
}

module.exports = {
  document: { createElement: makeElement },
  makeElement: makeElement
};
