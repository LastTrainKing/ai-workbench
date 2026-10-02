import { Modal } from '@/components/ui/overlay'
import { useUiStore } from '@/store/ui'

const SHORTCUT_GROUPS: { title: string; items: { keys: string[]; label: string }[] }[] = [
  {
    title: '全局',
    items: [
      { keys: ['Ctrl', 'K'], label: '打开 / 关闭命令面板' },
      { keys: ['/'], label: '聚焦全局检索' },
      { keys: ['?'], label: '显示快捷键帮助' },
      { keys: ['Esc'], label: '关闭当前浮层' },
    ],
  },
  {
    title: '命令面板',
    items: [
      { keys: ['↑', '↓'], label: '上下选择结果' },
      { keys: ['↵'], label: '执行选中项' },
    ],
  },
  {
    title: '文档',
    items: [{ keys: ['拖拽'], label: '把文件拖到上传区即可入库' }],
  },
]

export function ShortcutsOverlay() {
  const open = useUiStore((state) => state.shortcutsOpen)
  const setOpen = useUiStore((state) => state.setShortcuts)

  return (
    <Modal
      open={open}
      onClose={() => setOpen(false)}
      title="键盘快捷键"
      description="在工作台中随时使用这些组合键"
      size="md"
    >
      <div className="grid gap-5 sm:grid-cols-2">
        {SHORTCUT_GROUPS.map((group) => (
          <section key={group.title}>
            <h3 className="mb-2 text-2xs font-semibold uppercase tracking-wider text-faint">
              {group.title}
            </h3>
            <ul className="space-y-1.5">
              {group.items.map((item) => (
                <li key={item.label} className="flex items-center justify-between gap-3">
                  <span className="text-xs text-muted">{item.label}</span>
                  <span className="flex shrink-0 items-center gap-0.5">
                    {item.keys.map((key) => (
                      <kbd key={key} className="aiw-kbd font-sans">
                        {key}
                      </kbd>
                    ))}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </Modal>
  )
}
