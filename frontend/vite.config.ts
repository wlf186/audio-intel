import { defineConfig } from 'vite'
export default defineConfig({esbuild:{jsx:'automatic'},server:{proxy:{'/api':'http://127.0.0.1:20810','/v1':'http://127.0.0.1:20810'}}})
