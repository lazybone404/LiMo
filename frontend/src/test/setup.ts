import '@testing-library/jest-dom'

// jsdom does not provide scrollIntoView（标签栏用它把激活标签滚进可视区；
// 真实浏览器全都有，缺的只是 jsdom 的实现）
Element.prototype.scrollIntoView = () => {}

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
