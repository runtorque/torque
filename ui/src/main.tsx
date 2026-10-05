import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import { App } from './app/App';
import { applyAppearance, readAppearance } from './app/preferences';
import './design/globals.css';

const root = document.getElementById('root');
if (!root) throw new Error('Torque UI root element is missing');

applyAppearance(readAppearance());

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
