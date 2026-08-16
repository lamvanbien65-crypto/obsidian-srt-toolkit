// 命令面板注册
import type { SrtPlugin } from "./main";
import { BiliModal, FileSuggestModal } from "./modals";
import { activateTaskView } from "./task-view";

export function registerCommands(plugin: SrtPlugin): void {
  const app = plugin.app;

  plugin.addCommand({
    id: "bili-onestop",
    name: "B站一键剪藏",
    callback: () => new BiliModal(app, plugin, "bili-onestop").open(),
  });
  plugin.addCommand({
    id: "bili-download",
    name: "B站下载视频",
    callback: () => new BiliModal(app, plugin, "bili-download").open(),
  });
  plugin.addCommand({
    id: "bili-subtitle",
    name: "B站抓取字幕",
    callback: () => new BiliModal(app, plugin, "bili-subtitle").open(),
  });
  plugin.addCommand({
    id: "transcribe",
    name: "视频转录",
    callback: () => new FileSuggestModal(app, plugin, "transcribe").open(),
  });
  plugin.addCommand({
    id: "standard-clip",
    name: "标准字幕剪藏",
    callback: () => new FileSuggestModal(app, plugin, "standard").open(),
  });
  plugin.addCommand({
    id: "open-task-view",
    name: "打开任务面板",
    callback: () => activateTaskView(plugin),
  });
  plugin.addCommand({
    id: "cancel-all",
    name: "取消全部任务",
    callback: () => {
      if (window.confirm("确定取消全部任务？")) plugin.queue.cancelAll();
    },
  });
}
