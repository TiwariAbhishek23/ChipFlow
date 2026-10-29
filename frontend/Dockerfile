FROM node:20-slim

WORKDIR /app
COPY frontend/package*.json /app/
RUN npm install
COPY frontend /app
ENV NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
EXPOSE 3000
CMD ["npm", "run", "dev"]
