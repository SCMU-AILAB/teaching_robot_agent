import Cocoa
import FlutterMacOS

class MainFlutterWindow: NSWindow {
  override func awakeFromNib() {
    let flutterViewController = FlutterViewController()
    let windowFrame = self.frame
    self.contentViewController = flutterViewController
    self.setFrame(windowFrame, display: true)

    // 课堂页是三栏响应式布局，Flutter 默认的 800x600 会把对话区挤到窗口外，
    // 这里按常见笔记本屏幕给一个能完整展示的默认尺寸，并限制最小尺寸防止布局塌陷。
    self.setContentSize(NSSize(width: 1280, height: 900))
    self.minSize = NSSize(width: 960, height: 680)
    self.center()

    RegisterGeneratedPlugins(registry: flutterViewController)

    super.awakeFromNib()
  }
}
