import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import Admin from './Admin.jsx'

const isAdmin = window.location.pathname === '/admin'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    {isAdmin ? <Admin /> : <App />}
  </StrictMode>,
)
