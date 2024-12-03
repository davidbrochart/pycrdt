use pyo3::prelude::*;
use pyo3::exceptions::PyValueError;
use pyo3::types::{PyBytes, PyTuple};
use yrs::{diff_updates_v1, encode_state_vector_from_update_v1, merge_updates_v1};

#[pyfunction]
pub fn merge_updates(updates: &Bound<'_, PyTuple>) -> PyResult<PyObject> {
    let updates: Vec<Vec<u8>> = updates.extract().unwrap();
    let Ok(update) = merge_updates_v1(&updates) else {
        return Err(PyValueError::new_err("Cannot merge updates"));
    };
    let bytes: PyObject = Python::with_gil(|py| PyBytes::new(py, &update).into());
    Ok(bytes)
}

#[pyfunction]
pub fn get_state(update: &Bound<'_, PyBytes>) -> PyResult<PyObject> {
    let update: &[u8] = update.extract()?;
    let Ok(u) = encode_state_vector_from_update_v1(&update) else {
        return Err(PyValueError::new_err(
            "Cannot encode state vector from update",
        ));
    };
    let bytes: PyObject = Python::with_gil(|py| PyBytes::new(py, &u).into());
    Ok(bytes)
}

#[pyfunction]
pub fn get_update(update: &Bound<'_, PyBytes>, state: &Bound<'_, PyBytes>) -> PyResult<PyObject> {
    let update: &[u8] = update.extract()?;
    let state: &[u8] = state.extract()?;
    let Ok(u) = diff_updates_v1(&update, &state) else {
        return Err(PyValueError::new_err("Cannot diff updates"));
    };
    let bytes: PyObject = Python::with_gil(|py| PyBytes::new(py, &u).into());
    Ok(bytes)
}
