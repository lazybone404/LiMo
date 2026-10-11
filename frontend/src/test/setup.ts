import '@testing-library/jest-dom'

// jsdom does not provide scrollIntoView（标签栏用它把激活标签滚进可视区；
// 真实浏览器全都有，缺的只是 jsdom 的实现）
Element.prototype.scrollIntoView = () => {}

// jsdom does not provide ResizeObserver（预览区的图片组件 react-medium-image-zoom
// 在 mount 时用它量元素尺寸，PreviewArea 也用它追踪面板宽度；真实浏览器全都有，
// 缺的只是 jsdom 的实现）。⚠️ 缺它时渲染**含 `<img>` 的 Markdown** 会整条用例崩在
// commit 阶段 —— 症状是 `ReferenceError: ResizeObserver is not defined`，
// 与「标记解析」毫无关系，别往那上面查。
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver

// jsdom does not provide matchMedia — mock it for all tests
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: query.includes('dark'),
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
})
