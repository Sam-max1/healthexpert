#!/bin/bash
set -e

echo "Starting Document AI Expert in Docker..."

# Start embed_llm on port 8003
echo "Starting embed_llm (port 8003)..."
python agents/embed_llm.py &

# Start gen_llm on port 8002
echo "Starting gen_llm (port 8002)..."
python agents/gen_llm.py &

# Give the LLM microservices a moment to initialize
echo "Waiting for LLM microservices to boot..."
sleep 5

# Start Flask app on PORT (default 7860 for HF Spaces)
echo "Starting Flask web application..."
python app.py &

# Wait for any process to exit
wait -n
  
# Exit with status of process that exited first
exit $?
