# ASTRA AI - Master Specification v2 Deployment Guide

## Overview

This guide provides instructions for deploying the Master Specification v2 implementation of ASTRA AI.

## Prerequisites

### Python Version
- Python 3.10 or higher

### Required Dependencies

```bash
# Core dependencies
pip install numpy scipy pandas scikit-learn

# Existing ASTRA dependencies
pip install yaml pyyaml asyncpg sqlalchemy prometheus-client fastapi uvicorn

# Optional dependencies for full functionality
pip install pytest pytest-asyncio
```

## Installation

### 1. Clone the Repository
```bash
git clone <repository-url>
cd astra_bot
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Install New Dependencies
```bash
pip install numpy scipy pandas scikit-learn
```

## Configuration

### Environment Variables

Create a `.env` file based on `.env.example`:

```bash
# Database
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/astra_bot

# Trading
API_KEY=your_exchange_api_key
API_SECRET=your_exchange_api_secret
PASSPHRASE=your_passphrase

# Logging
LOG_LEVEL=INFO
LOG_DIR=/tmp/logs

# Web Server
PORT=8000
```

### Configuration Files

The main configuration is in `config/` directory. Update as needed for your environment.

## Running the System

### Development Mode

```bash
# Run the main application
python main.py

# Or with uvicorn directly
uvicorn main:app --reload --port 8000
```

### Production Mode

```bash
# Build and run with Docker
docker build -t astra-bot .
docker run -p 8000:8000 astra-bot

# Or with docker-compose
docker-compose up -d
```

## Testing

### Run Unit Tests

```bash
# Install pytest
pip install pytest pytest-asyncio

# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_new_engines.py -v
```

### Manual Testing

You can test individual components:

```python
from astra_bot.engines.uncertainty_engine import get_uncertainty_engine

# Get the engine
engine = get_uncertainty_engine()

# Use it
# ... (see component documentation)
```

## Integration with Existing System

### Phase 1: Statistical Robustness

Add to your validation pipeline:

```python
from astra_bot.research.statistical_tests import get_statistical_tests

stat_tests = get_statistical_tests()

# Validate strategy
results = stat_tests.validate_strategy(returns, parameters)
print(results)
```

### Phase 2: Prediction Quality

Enhance your prediction pipeline:

```python
from astra_bot.engines.uncertainty_engine import get_uncertainty_engine
from astra_bot.engines.probabilistic_forecast import get_forecast_engine

uncertainty_engine = get_uncertainty_engine()
forecast_engine = get_forecast_engine()

# Calculate uncertainty
uncertainty = uncertainty_engine.assess_uncertainty(...)

# Create probabilistic forecast
forecast = forecast_engine.create_multi_horizon_forecast(...)
```

### Phase 3: Decision Intelligence

Improve decision making:

```python
from astra_bot.engines.opportunity_cost_engine import get_opportunity_cost_engine

opportunity_engine = get_opportunity_cost_engine()

# Evaluate opportunities
result = opportunity_engine.evaluate_signals(opportunities, total_capital)
```

### Phase 4: Execution

Optimize execution:

```python
from astra_bot.engines.execution_optimizer import get_execution_optimizer

optimizer = get_execution_optimizer()

# Select optimal strategy
plan = optimizer.select_optimal_strategy(signal, order_book, liquidity, urgency, expected_edge, position_size)
```

## Monitoring

### Health Checks

```bash
# Check system health
curl http://localhost:8000/health

# Check status
curl http://localhost:8000/status

# Get metrics
curl http://localhost:8000/metrics
```

### Logging

Logs are written to the directory specified in `LOG_DIR` environment variable or `/tmp/logs` by default.

## Troubleshooting

### Common Issues

1. **Missing Dependencies**: Install required packages with `pip install numpy scipy pandas scikit-learn`

2. **Database Connection**: Check your `DATABASE_URL` environment variable

3. **API Keys**: Ensure your exchange API keys are correctly configured

4. **Import Errors**: Make sure all files are in the correct location

### Debug Mode

Set `LOG_LEVEL=DEBUG` in your environment to get more detailed logging.

## Phased Deployment

### Phase A: Statistical Robustness (Week 1)
- Deploy Statistical Tests module
- Integrate with existing validation
- Test CPCV, PBO, DSR

### Phase B: Prediction Quality (Week 2)
- Deploy Uncertainty Engine
- Deploy Probabilistic Forecast Engine
- Deploy Regime Similarity Engine
- Integrate with prediction pipeline

### Phase C: Decision Intelligence (Week 3)
- Deploy Opportunity Cost Engine
- Integrate with decision pipeline
- Test capital allocation

### Phase D: Execution (Week 4)
- Deploy Alpha Decay Engine
- Deploy Execution Optimizer
- Integrate with execution pipeline
- Test different execution strategies

### Phase E: Portfolio (Week 5)
- Deploy Signal Correlation Engine
- Deploy Portfolio Exposure Engine
- Deploy Tail Risk Engine
- Integrate with risk management

### Phase F: Learning (Week 6)
- Deploy MFE/MAE Engine
- Deploy Counterfactual Engine
- Deploy Loss Attribution Engine
- Integrate with trade analysis

### Phase G: Discovery (Week 7)
- Deploy Market State Clusterer
- Integrate with market analysis
- Test clustering algorithms

### Phase H: Autonomous Research (Week 8)
- Deploy Experiment Registry
- Deploy Hypothesis Generator
- Deploy Research Agent
- Enable autonomous research

## Rollback

If any phase causes issues, you can:

1. Revert to previous version
2. Disable specific engines
3. Use feature flags to enable/disable functionality

## Performance Tuning

Each engine has configurable parameters. Tune them based on your:
- Market conditions
- Trading style
- Risk tolerance
- Capital size

## Security

### API Security
- Keep API keys secret
- Use environment variables
- Rotate keys regularly

### Database Security
- Use strong passwords
- Restrict database access
- Backup regularly

## Updates

### Version Compatibility

This implementation is backward compatible with the existing ASTRA system. All new components are additive.

### Update Procedure

1. Backup your database
2. Pull latest code
3. Run tests
4. Deploy to staging
5. Test thoroughly
6. Deploy to production

## Support

For issues or questions:
- Check the documentation
- Review logs
- Check the issue tracker
- Contact the development team

## License

This software is proprietary and confidential. Do not distribute without permission.

---

**ASTRA AI - Building the Future of Autonomous Trading**
