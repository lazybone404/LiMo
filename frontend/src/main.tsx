import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import 'github-markdown-css/github-markdown-light.css'
import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
