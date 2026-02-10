"""
SIR (Susceptible-Infected-Recovered) Epidemic Model for Team Contagion

This module implements a real SIR model to predict burnout/risk spread
across a team network based on epidemiological math.

Theory:
- S(usceptible): Healthy employees who could become at-risk
- I(nfected): Employees currently at elevated/critical risk  
- R(ecovered): Employees who returned to healthy status

Differential equations:
    dS/dt = -β * S * I / N
    dI/dt = β * S * I / N - γ * I
    dR/dt = γ * I

Where:
    β = transmission rate (contact rate × probability of infection)
    γ = recovery rate (1 / average recovery time)
    N = total population
"""
import numpy as np
from scipy.integrate import odeint
from typing import Dict, List, Tuple
from dataclasses import dataclass


@dataclass
class SIRResult:
    """Result of SIR simulation"""
    days: List[int]
    susceptible: List[float]
    infected: List[float]
    recovered: List[float]
    peak_day: int
    peak_infected: float
    r0: float  # Basic reproduction number


class SIRSimulator:
    """
    Simulates burnout/risk contagion using the SIR epidemic model.
    
    Usage:
        simulator = SIRSimulator(beta=0.3, gamma=0.1)
        result = simulator.run(
            total_population=50,
            initial_infected=3,
            days=30
        )
    """
    
    def __init__(self, beta: float = 0.3, gamma: float = 0.1):
        """
        Initialize the SIR model.
        
        Args:
            beta: Transmission rate (probability of spreading per contact per day)
                  Higher values = faster spread. Typical range: 0.1-0.5
            gamma: Recovery rate (1 / average days to recover)
                   Higher values = faster recovery. Typical range: 0.05-0.2
        """
        self.beta = beta
        self.gamma = gamma
    
    @property
    def r0(self) -> float:
        """
        Basic reproduction number (R0).
        R0 > 1 means epidemic will spread.
        R0 < 1 means epidemic will die out.
        """
        return self.beta / self.gamma
    
    def _sir_derivatives(self, y: Tuple[float, float, float], t: float, 
                          N: float, beta: float, gamma: float) -> Tuple[float, float, float]:
        """
        Calculate derivatives for SIR model.
        
        Args:
            y: Tuple of (S, I, R) values
            t: Time point (unused but required by odeint)
            N: Total population
            beta: Transmission rate
            gamma: Recovery rate
        
        Returns:
            Tuple of (dS/dt, dI/dt, dR/dt)
        """
        S, I, R = y
        
        # Force of infection
        dS = -beta * S * I / N
        dI = beta * S * I / N - gamma * I
        dR = gamma * I
        
        return dS, dI, dR
    
    def run(self, total_population: int, initial_infected: int, 
            days: int = 30) -> SIRResult:
        """
        Run SIR simulation.
        
        Args:
            total_population: Total team size (N)
            initial_infected: Number of people currently at risk (I0)
            days: Number of days to simulate
        
        Returns:
            SIRResult with time series data and summary statistics
        """
        N = total_population
        I0 = initial_infected
        R0 = 0  # Initial recovered
        S0 = N - I0 - R0  # Initial susceptible
        
        # Time grid
        t = np.linspace(0, days, days + 1)
        
        # Initial conditions
        y0 = (S0, I0, R0)
        
        # Integrate the SIR equations
        solution = odeint(
            self._sir_derivatives, 
            y0, 
            t, 
            args=(N, self.beta, self.gamma)
        )
        
        S, I, R = solution.T
        
        # Find peak infection
        peak_idx = np.argmax(I)
        
        return SIRResult(
            days=list(range(days + 1)),
            susceptible=S.tolist(),
            infected=I.tolist(),
            recovered=R.tolist(),
            peak_day=int(peak_idx),
            peak_infected=float(I[peak_idx]),
            r0=self.r0
        )
    
    @classmethod
    def from_team_data(cls, 
                       avg_connections: float,
                       avg_risk_score: float,
                       avg_recovery_days: float = 14) -> 'SIRSimulator':
        """
        Create a SIRSimulator calibrated from real team data.
        
        Args:
            avg_connections: Average number of connections per person in the graph
            avg_risk_score: Average risk score of team (0-1)
            avg_recovery_days: Average days it takes for someone to recover from burnout
        
        Returns:
            Calibrated SIRSimulator instance
        """
        # Beta = contact rate * transmission probability per contact
        # More connections = higher contact rate
        # Higher average risk = higher transmission probability
        contact_rate = min(avg_connections / 10, 1.0)  # Normalize to 0-1
        transmission_prob = 0.2 + (avg_risk_score * 0.3)  # 0.2-0.5 range
        beta = contact_rate * transmission_prob
        
        # Gamma = 1 / recovery time
        gamma = 1 / avg_recovery_days
        
        return cls(beta=beta, gamma=gamma)


def predict_contagion_risk(
    total_members: int,
    infected_count: int,
    avg_connections: float = 3.0,
    avg_risk_score: float = 0.3,
    days: int = 30
) -> Dict:
    """
    High-level function to predict contagion risk for a team.
    
    Args:
        total_members: Total team size
        infected_count: Number of elevated/critical risk members
        avg_connections: Average connections in network graph
        avg_risk_score: Average risk score (0-1)
        days: Forecast horizon
    
    Returns:
        Dict with prediction results
    """
    if total_members < 3 or infected_count < 1:
        return {
            "status": "INSUFFICIENT_DATA",
            "message": "Need at least 3 members and 1 at-risk person for prediction"
        }
    
    simulator = SIRSimulator.from_team_data(
        avg_connections=avg_connections,
        avg_risk_score=avg_risk_score
    )
    
    result = simulator.run(
        total_population=total_members,
        initial_infected=infected_count,
        days=days
    )
    
    # Determine risk level based on R0 and peak
    if result.r0 > 1.5 and result.peak_infected > total_members * 0.3:
        risk_level = "CRITICAL"
    elif result.r0 > 1.0 and result.peak_infected > total_members * 0.15:
        risk_level = "ELEVATED"
    else:
        risk_level = "LOW"
    
    return {
        "status": "OK",
        "risk_level": risk_level,
        "r0": round(result.r0, 2),
        "peak_day": result.peak_day,
        "peak_infected": round(result.peak_infected, 1),
        "forecast": {
            "days": result.days,
            "susceptible": [round(x, 1) for x in result.susceptible],
            "infected": [round(x, 1) for x in result.infected],
            "recovered": [round(x, 1) for x in result.recovered],
        }
    }
