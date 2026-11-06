# Unit testing
set(
  tests
  "tblite"
  "gfnff"
  "gfn0"
  "gfn0occ"
  "CN"
  "optimization"
)
set(
  test-srcs
  "testmol.f90"
  "helpers.f90"
  "main.f90"
)
foreach(t IN LISTS tests)
  string(MAKE_C_IDENTIFIER ${t} t) 
  list(APPEND test-srcs "test_${t}.F90")
endforeach()

add_executable(
  "${PROJECT_NAME}-tester"
  "${test-srcs}"
)
target_link_libraries(
  "${PROJECT_NAME}-tester"
  PRIVATE
  "lib-${PROJECT_NAME}-static"
  "test-drive::test-drive"
)
if(DEFINED crest_windows_link_options)
  target_link_options("${PROJECT_NAME}-tester" PRIVATE ${crest_windows_link_options})
endif()

foreach(t IN LISTS tests)
  add_test("${PROJECT_NAME}/${t}" "${PROJECT_NAME}-tester" "${t}")
endforeach()
