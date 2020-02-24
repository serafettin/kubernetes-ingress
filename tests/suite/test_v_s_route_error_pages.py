import requests
import pytest
import json

from settings import TEST_DATA
from suite.custom_assertions import wait_and_assert_status_code, \
    assert_event_starts_with_text_and_contains_errors
from suite.custom_resources_utils import get_vs_nginx_template_conf, patch_v_s_route_from_yaml, \
    patch_virtual_server_from_yaml
from suite.resources_utils import get_first_pod_name, get_events, wait_before_test


@pytest.mark.vsr
@pytest.mark.parametrize('crd_ingress_controller, v_s_route_setup',
                         [({"type": "complete", "extra_args": [f"-enable-custom-resources"]},
                           {"example": "virtual-server-route-error-pages"})],
                         indirect=True)
class TestVSRErrorPages:
    def test_redirect_strategy(self, kube_apis, ingress_controller_prerequisites, crd_ingress_controller,
                               v_s_route_setup):
        req_url = f"http://{v_s_route_setup.public_endpoint.public_ip}:{v_s_route_setup.public_endpoint.port}"
        wait_and_assert_status_code(307, f"{req_url}{v_s_route_setup.route_m.paths[0]}",
                                    v_s_route_setup.vs_host, allow_redirects=False)
        resp = requests.get(f"{req_url}{v_s_route_setup.route_m.paths[0]}",
                            headers={"host": v_s_route_setup.vs_host}, allow_redirects=False)
        assert f'http://{v_s_route_setup.vs_host}/error_307.html' in resp.next.url

    def test_return_strategy(self, kube_apis, ingress_controller_prerequisites, crd_ingress_controller,
                             v_s_route_setup):
        req_url = f"http://{v_s_route_setup.public_endpoint.public_ip}:{v_s_route_setup.public_endpoint.port}"
        wait_and_assert_status_code(207, f"{req_url}{v_s_route_setup.route_m.paths[1]}", v_s_route_setup.vs_host)
        resp = requests.get(f"{req_url}{v_s_route_setup.route_m.paths[1]}",
                            headers={"host": v_s_route_setup.vs_host})
        resp_content = json.loads(resp.content)
        assert resp_content['status'] == '207' \
            and resp_content['message'] == 'Forbidden' \
            and resp.headers.get('x-debug-original-status') == '207'

    def test_virtual_server_after_update(self, kube_apis, ingress_controller_prerequisites, crd_ingress_controller,
                                         v_s_route_setup):
        req_url = f"http://{v_s_route_setup.public_endpoint.public_ip}:{v_s_route_setup.public_endpoint.port}"
        patch_v_s_route_from_yaml(kube_apis.custom_objects,
                                  v_s_route_setup.route_m.name,
                                  f"{TEST_DATA}/virtual-server-route-error-pages/route-multiple-updated.yaml",
                                  v_s_route_setup.route_m.namespace)
        wait_and_assert_status_code(301, f"{req_url}{v_s_route_setup.route_m.paths[0]}",
                                    v_s_route_setup.vs_host, allow_redirects=False)
        resp = requests.get(f"{req_url}{v_s_route_setup.route_m.paths[0]}",
                            headers={"host": v_s_route_setup.vs_host, "x-forwarded-proto": "http"},
                            allow_redirects=False)
        assert f'http://{v_s_route_setup.vs_host}/error_301_http.html' in resp.next.url

        wait_and_assert_status_code(502, f"{req_url}{v_s_route_setup.route_m.paths[1]}",
                                    v_s_route_setup.vs_host, allow_redirects=False)
        resp = requests.get(f"{req_url}{v_s_route_setup.route_m.paths[1]}",
                            headers={"host": v_s_route_setup.vs_host})
        resp_content = resp.content.decode('utf-8')
        assert resp_content == 'Hello World!\n'

    def test_validation_event_flow(self, kube_apis, ingress_controller_prerequisites, crd_ingress_controller,
                                   v_s_route_setup):
        err_text = "Invalid value: \"schema\": " \
                   "'schema' contains an invalid NGINX variable. Accepted variables are: status"
        invalid_fields_m = [
            f"spec.subroutes[0].errorPages[0].codes: Required value: must include at least 1 status code in `codes",
            f"spec.subroutes[1].errorPages[0].return.body: Required value",
            f"spec.subroutes[1].errorPages[0].return.headers[0].value: {err_text}"
        ]
        invalid_fields_s = [
            "spec.subroutes[0].errorPages[0].redirect.url: Required value: must specify a url"
        ]
        text_s = f"{v_s_route_setup.route_s.namespace}/{v_s_route_setup.route_s.name}"
        text_m = f"{v_s_route_setup.route_m.namespace}/{v_s_route_setup.route_m.name}"
        vsr_s_event_text = f"VirtualServerRoute {text_s} is invalid and was rejected: "
        vsr_m_event_text = f"VirtualServerRoute {text_m} is invalid and was rejected: "
        patch_v_s_route_from_yaml(kube_apis.custom_objects,
                                  v_s_route_setup.route_s.name,
                                  f"{TEST_DATA}/virtual-server-route-error-pages/route-single-invalid.yaml",
                                  v_s_route_setup.route_s.namespace)
        patch_v_s_route_from_yaml(kube_apis.custom_objects,
                                  v_s_route_setup.route_m.name,
                                  f"{TEST_DATA}/virtual-server-route-error-pages/route-multiple-invalid.yaml",
                                  v_s_route_setup.route_m.namespace)
        wait_before_test(2)
        ic_pod_name = get_first_pod_name(kube_apis.v1, ingress_controller_prerequisites.namespace)
        config = get_vs_nginx_template_conf(kube_apis.v1,
                                            v_s_route_setup.namespace,
                                            v_s_route_setup.vs_name,
                                            ic_pod_name,
                                            ingress_controller_prerequisites.namespace)
        vsr_s_events = get_events(kube_apis.v1, v_s_route_setup.route_s.namespace)
        vsr_m_events = get_events(kube_apis.v1, v_s_route_setup.route_m.namespace)

        assert_event_starts_with_text_and_contains_errors(vsr_s_event_text, vsr_s_events, invalid_fields_s)
        assert_event_starts_with_text_and_contains_errors(vsr_m_event_text, vsr_m_events, invalid_fields_m)
        assert "upstream" not in config

    @pytest.mark.parametrize('v_s_r_data', [
        {"src": "route-multiple-splits.yaml", "expected_code": 308},
        {"src": "route-multiple-matches.yaml", "expected_code": 307}
    ])
    def test_splits_and_matches(self, kube_apis, ingress_controller_prerequisites, crd_ingress_controller,
                                v_s_route_setup, v_s_r_data):
        req_url = f"http://{v_s_route_setup.public_endpoint.public_ip}:{v_s_route_setup.public_endpoint.port}"
        patch_v_s_route_from_yaml(kube_apis.custom_objects,
                                  v_s_route_setup.route_m.name,
                                  f"{TEST_DATA}/virtual-server-route-error-pages/{v_s_r_data['src']}",
                                  v_s_route_setup.route_m.namespace)
        wait_and_assert_status_code(v_s_r_data["expected_code"], f"{req_url}{v_s_route_setup.route_m.paths[0]}",
                                    v_s_route_setup.vs_host, allow_redirects=False)
        resp = requests.get(f"{req_url}{v_s_route_setup.route_m.paths[0]}",
                            headers={"host": v_s_route_setup.vs_host}, allow_redirects=False)
        assert f'http://{v_s_route_setup.vs_host}/error_{v_s_r_data["expected_code"]}.html' in resp.next.url

    def test_vsr_overrides_vs(self, kube_apis, ingress_controller_prerequisites, crd_ingress_controller,
                              v_s_route_setup):
        req_url = f"http://{v_s_route_setup.public_endpoint.public_ip}:{v_s_route_setup.public_endpoint.port}"
        vs_src = f"{TEST_DATA}/virtual-server-route-error-pages/standard/virtual-server-updated.yaml"
        patch_virtual_server_from_yaml(kube_apis.custom_objects,
                                       v_s_route_setup.vs_name,
                                       vs_src,
                                       v_s_route_setup.namespace)
        patch_v_s_route_from_yaml(kube_apis.custom_objects,
                                  v_s_route_setup.route_m.name,
                                  f"{TEST_DATA}/virtual-server-route-error-pages/route-multiple.yaml",
                                  v_s_route_setup.route_m.namespace)
        wait_and_assert_status_code(307, f"{req_url}{v_s_route_setup.route_m.paths[0]}",
                                    v_s_route_setup.vs_host, allow_redirects=False)
        resp = requests.get(f"{req_url}{v_s_route_setup.route_m.paths[0]}",
                            headers={"host": v_s_route_setup.vs_host}, allow_redirects=False)
        assert f'http://{v_s_route_setup.vs_host}/error_307.html' in resp.next.url

