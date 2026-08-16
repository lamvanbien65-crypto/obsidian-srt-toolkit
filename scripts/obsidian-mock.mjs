// Obsidian API mock（严格契约加载测试用）
class Plugin {
  constructor(app, manifest) { this.app = app; this.manifest = manifest; }
  registerView() {}
  registerEvent() {}
  registerDomEvent() {}
  registerInterval() {}
  addCommand() {}
  addSettingTab() {}
  addRibbonIcon() { return { addClass() {} }; }
  addStatusBarItem() { return { addClass() {}, setText() {}, onclick: null }; }
  loadData() { return Promise.resolve(null); }
  saveData() { return Promise.resolve(); }
}

class Setting {
  constructor(containerEl) { this.containerEl = containerEl; this.settingEl = { style: { display: "" } }; }
  setDesc() { return this; }
  setName() { return this; }
  addText() { return { setPlaceholder() { return this; }, setValue() { return this; }, onChange() {} }; }
  addTextArea() { return { setPlaceholder() { return this; }, setValue() { return this; }, onChange() {}, inputEl: { rows: 0, style: {} } }; }
  addDropdown() { return { addOption() { return this; }, setValue() { return this; }, onChange() {} }; }
  addToggle() { return { setValue() { return this; }, onChange() {} }; }
  addButton() { return { setButtonText() { return this; }, setCta() { return this; }, onClick() {} }; }
}

class PluginSettingTab {
  constructor(app, plugin) { this.app = app; this.plugin = plugin; }
  display() {}
}

class Modal {
  constructor(app) { this.app = app; this.contentEl = { empty() {}, createEl() { return new El(); } }; }
  open() { return this; }
  close() {}
}
class El {
  constructor() { this.settingEl = { style: { display: "" } }; }
  empty() { return this; }
  createEl() { return new El(); }
  createDiv() { return new El(); }
  setDesc() { return this; }
  setName() { return this; }
  setText() { return this; }
  addClass() { return this; }
  addText() { return { setPlaceholder() { return this; }, setValue() { return this; }, onChange() {} }; }
  addTextArea() { return { setPlaceholder() { return this; }, setValue() { return this; }, onChange() {}, inputEl: { rows: 0, style: {} } }; }
  addDropdown() { return { addOption() { return this; }, setValue() { return this; }, onChange() {} }; }
  addToggle() { return { setValue() { return this; }, onChange() {} }; }
  addButton() { return { setButtonText() { return this; }, setCta() { return this; }, onClick() {} }; }
  addClass() {}
}

class SuggestModal {
  constructor(app) { this.app = app; }
  setPlaceholder() {}
  setInstructions() {}
  open() {}
}

class ItemView {
  constructor(leaf) {
    this.leaf = leaf;
    this.containerEl = { children: [null, new El()] };
  }
  getViewType() { return ""; }
  getDisplayText() { return ""; }
  getIcon() { return ""; }
  async onOpen() {}
  async onClose() {}
}

class Notice {
  constructor(msg) { console.log("[Notice]", msg); }
}
function normalizePath(p) { return p.replace(/\\/g, "/"); }

export default { Plugin, PluginSettingTab, Setting, Modal, SuggestModal, ItemView, Notice, normalizePath };
